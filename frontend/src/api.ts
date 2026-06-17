export interface Offer {
  product_name: string;
  description?: string | null;
  brand?: string | null;
  retailer?: string | null;
  price?: number | null;
  old_price?: number | null;
  unit?: string | null;
  valid_to?: string | null;
  discount_pct?: number | null;
}

export interface ItemResult {
  item: string;
  offers: Offer[];
}

export type SearchEvent =
  | { event: "progress"; step: string; label: string; detail: Record<string, unknown> }
  | { event: "result"; zip_code: string | null; items: string[]; results: ItemResult[] }
  | { event: "error"; message: string }
  | { event: "done" };

export interface SearchBody {
  location: string;
  query: string;
  mode: "list" | "recipe";
}

export async function search(body: SearchBody, onEvent: (e: SearchEvent) => void): Promise<void> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as SearchEvent);
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
