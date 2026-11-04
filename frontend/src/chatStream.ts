export type ChatEvent =
  | { type: "start" }
  | { type: "status"; text: string }
  | { type: "delta"; text: string }
  | { type: "done"; answer: string; suggested_reply: string | null; draft_id: number | null; sources?: { id: number; subject: string }[] }
  | { type: "error"; message: string };

export async function readChatEvents(body: ReadableStream<Uint8Array>, onEvent: (event: ChatEvent) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let complete = false;
  const consume = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as ChatEvent;
    if (event.type === "error") throw new Error(event.message);
    if (event.type === "done") complete = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        consume(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1);
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!complete) throw new Error("The response was interrupted. Please try again.");
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
