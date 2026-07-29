export type TutorStreamEvent = {
  type:
    | "run.started"
    | "node.started"
    | "retrieval.completed"
    | "teacher.delta"
    | "node.completed"
    | "run.completed"
    | "run.failed"
    | "run.cancelled";
  data: Record<string, unknown>;
};

export function consumeTutorEventStream(
  response: Response,
  onEvent: (event: TutorStreamEvent) => void,
): Promise<void>;
