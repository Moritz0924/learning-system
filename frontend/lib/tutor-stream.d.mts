export type TutorStreamEvent = {
  type:
    | "run.started"
    | "node.started"
    | "retrieval.completed"
    | "teacher.delta"
    | "node.completed"
    | "run.completed"
    | "run.failed"
    | "run.cancelled"
    | "tool.approval_required"
    | "run.awaiting_approval"
    | "tool.started"
    | "tool.completed";
  data: Record<string, unknown>;
};

export type TutorStreamRequest = {
  requestId: string;
  threadId: string;
  runId: string | null;
  controller: AbortController;
};

export function isTutorStreamCurrent(
  activeRequest: TutorStreamRequest | null,
  request: TutorStreamRequest,
  activeThreadId: string,
): boolean;

export function cancelTutorRequest(
  request: TutorStreamRequest | null,
  cancelRequest: (runId: string) => Promise<unknown>,
): Promise<void>;

export function consumeTutorEventStream(
  response: Response,
  onEvent: (event: TutorStreamEvent) => void,
): Promise<void>;
