export function DocumentError({ message, testId }: { message: string; testId?: string }) {
  return (
    <p
      data-testid={testId}
      className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700"
      role="alert"
    >
      {message}
    </p>
  );
}
