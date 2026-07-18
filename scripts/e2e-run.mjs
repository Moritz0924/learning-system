import { spawn, spawnSync } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontend = path.join(root, "frontend");
const tmpRoot = path.join(root, ".tmp");
const tmpDir = path.join(tmpRoot, `e2e-${process.pid}-${Date.now()}`);
const nextEnvPath = path.join(frontend, "next-env.d.ts");
const nextEnvOriginal = existsSync(nextEnvPath) ? readFileSync(nextEnvPath) : null;
const backendPort = 8123;
const frontendPort = 3100;
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;

function resolvePython() {
  const configured = process.env.E2E_PYTHON?.trim();
  if (configured) return configured;
  const virtualenvPython = path.join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
  );
  if (existsSync(virtualenvPython)) return virtualenvPython;
  return process.platform === "win32" ? "python" : "python3";
}

async function assertPortAvailable(port, name) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", () => reject(new Error(`${name} port ${port} is already in use`)));
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}

function startLoggedProcess(command, args, { cwd, env, stdoutPath, stderrPath }) {
  const stdoutFd = openSync(stdoutPath, "w");
  const stderrFd = openSync(stderrPath, "w");
  const child = spawn(command, args, {
    cwd,
    env,
    stdio: ["ignore", stdoutFd, stderrFd],
    windowsHide: true,
    detached: process.platform !== "win32"
  });
  child.spawnError = null;
  child.once("error", (error) => {
    child.spawnError = error;
  });
  return { child, detached: process.platform !== "win32", stdoutFd, stderrFd, stdoutPath, stderrPath };
}

function startInheritedProcess(command, args, { cwd, env }) {
  const detached = process.platform !== "win32";
  const child = spawn(command, args, {
    cwd,
    env,
    stdio: "inherit",
    windowsHide: true,
    detached
  });
  return { child, detached };
}

async function waitForProcess(processRecord, name) {
  const { child } = processRecord;
  return await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal) {
        resolve(1);
        return;
      }
      resolve(code ?? 1);
    });
  }).catch((error) => {
    throw new Error(`${name} failed to start: ${error instanceof Error ? error.message : error}`);
  });
}

function logTail(filePath) {
  if (!existsSync(filePath)) return "";
  return readFileSync(filePath, "utf8").slice(-4000);
}

async function waitForUrl(url, name, processRecord, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { child, stderrPath } = processRecord;
    if (child.spawnError) throw child.spawnError;
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`${name} exited with code ${child.exitCode ?? child.signalCode}\n${logTail(stderrPath)}`);
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(5_000) });
      if (response.status < 500) return;
    } catch {
      // The service is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${name} did not become ready at ${url}\n${logTail(processRecord.stderrPath)}`);
}

async function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return true;
  return await new Promise((resolve) => {
    const timeout = setTimeout(() => {
      child.removeListener("exit", onExit);
      resolve(false);
    }, timeoutMs);
    const onExit = () => {
      clearTimeout(timeout);
      resolve(true);
    };
    child.once("exit", onExit);
  });
}

function processGroupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitForProcessGroupExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processGroupExists(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return !processGroupExists(pid);
}

async function stopProcessTree(processRecord) {
  if (!processRecord) return;
  const { child, detached, stdoutFd, stderrFd } = processRecord;
  if (child.pid) {
    if (process.platform === "win32") {
      if (child.exitCode === null && child.signalCode === null) {
        spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true
        });
        await waitForExit(child, 5_000);
      }
    } else if (detached) {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {
        // The process group has already exited.
      }
      if (!(await waitForProcessGroupExit(child.pid, 5_000))) {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          // The process group exited between the check and kill.
        }
        await waitForProcessGroupExit(child.pid, 2_000);
      }
    } else if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      if (!(await waitForExit(child, 5_000))) {
        child.kill("SIGKILL");
        await waitForExit(child, 2_000);
      }
    }
  }
  if (stdoutFd !== undefined) closeSync(stdoutFd);
  if (stderrFd !== undefined) closeSync(stderrFd);
}

const python = resolvePython();
mkdirSync(tmpDir, { recursive: true });
const databasePath = path.join(tmpDir, "playwright-e2e.db");
const objectStoragePath = path.join(tmpDir, "document_objects");

const env = {
  ...process.env,
  DATABASE_URL: `sqlite+pysqlite:///${databasePath.replaceAll("\\", "/")}`,
  CORS_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:${frontendPort}`,
  AUTH_ALLOWED_ORIGINS: `${frontendUrl},http://localhost:${frontendPort}`,
  PYTHONPATH: `${root}${path.delimiter}${path.join(root, "src")}`,
  DOCUMENT_OBJECT_STORAGE_BACKEND: "local",
  DOCUMENT_OBJECT_STORAGE_LOCAL_DIR: objectStoragePath,
  EMBEDDING_BACKEND: "deterministic",
  JWT_SECRET_KEY: "e2e-secret-key-that-is-long-enough-for-hs256",
  NEXT_PUBLIC_API_BASE_URL: backendUrl,
  PLAYWRIGHT_BASE_URL: frontendUrl,
  E2E_PYTHON: python
};

let backendProcess;
let frontendProcess;
let migrationProcess;
let playwrightProcess;
let cleanupPromise;
let exitCode = 1;

function restoreNextEnv() {
  if (nextEnvOriginal === null) {
    rmSync(nextEnvPath, { force: true });
    return;
  }
  writeFileSync(nextEnvPath, nextEnvOriginal);
}

function cleanup() {
  if (!cleanupPromise) {
    cleanupPromise = (async () => {
      await stopProcessTree(playwrightProcess);
      await stopProcessTree(frontendProcess);
      await stopProcessTree(backendProcess);
      await stopProcessTree(migrationProcess);
      restoreNextEnv();
    })();
  }
  return cleanupPromise;
}

function handleSignal(exitStatus) {
  void cleanup().finally(() => {
    console.error(`E2E artifacts preserved at ${tmpDir}`);
    process.exit(exitStatus);
  });
}

process.once("SIGINT", () => handleSignal(130));
process.once("SIGTERM", () => handleSignal(143));
process.once("SIGHUP", () => handleSignal(129));

try {
  await assertPortAvailable(backendPort, "backend");
  await assertPortAvailable(frontendPort, "frontend");

  migrationProcess = startInheritedProcess(
    python,
    ["-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
    { cwd: root, env }
  );
  const migrationStatus = await waitForProcess(migrationProcess, "Alembic");
  if (migrationStatus !== 0) throw new Error(`Alembic exited with code ${migrationStatus}`);
  migrationProcess = undefined;

  backendProcess = startLoggedProcess(
    python,
    ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", String(backendPort)],
    {
      cwd: root,
      env,
      stdoutPath: path.join(tmpDir, "e2e-backend.out.log"),
      stderrPath: path.join(tmpDir, "e2e-backend.err.log")
    }
  );
  await waitForUrl(`${backendUrl}/openapi.json`, "backend", backendProcess);

  frontendProcess = startLoggedProcess(
    process.execPath,
    [path.join(frontend, "node_modules", "next", "dist", "bin", "next"), "dev", "--hostname", "127.0.0.1", "--port", String(frontendPort)],
    {
      cwd: frontend,
      env,
      stdoutPath: path.join(tmpDir, "e2e-frontend.out.log"),
      stderrPath: path.join(tmpDir, "e2e-frontend.err.log")
    }
  );
  await waitForUrl(`${frontendUrl}/diagnosis`, "frontend", frontendProcess);

  playwrightProcess = startInheritedProcess(
    process.execPath,
    [path.join(frontend, "node_modules", "@playwright", "test", "cli.js"), "test", ...process.argv.slice(2)],
    { cwd: frontend, env }
  );
  exitCode = await waitForProcess(playwrightProcess, "Playwright");
  playwrightProcess = undefined;
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
} finally {
  await cleanup();
  if (exitCode === 0) {
    rmSync(tmpDir, { recursive: true, force: true });
  } else {
    console.error(`E2E artifacts preserved at ${tmpDir}`);
  }
}

process.exitCode = exitCode;
