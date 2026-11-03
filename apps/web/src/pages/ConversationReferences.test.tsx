import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ConversationReferences, referenceTitle } from "./ConversationReferences";

describe("ConversationReferences", () => {
  it("renders long titles in a constrained title element with the full hover text", () => {
    const title = "这是一个用于测试聊天消息网页引用区域是否能够正确限制宽度并在超出消息卡片时显示省略号的特别长网页标题";
    const html = renderToStaticMarkup(
      <ConversationReferences
        references={[{ type: "web", id: "https://example.test/news", title }]}
        onReference={vi.fn()}
      />,
    );

    expect(html).toContain('class="reference-list"');
    expect(html).toContain('class="reference-title"');
    expect(html).toContain(`title="${title}"`);
  });

  it("renders multiple web references with their original URLs and safe new tabs", () => {
    const html = renderToStaticMarkup(
      <ConversationReferences
        references={[
          { type: "web", id: "https://one.example/news", title: "第一条" },
          { type: "web", id: "https://two.example/news", title: "第二条" },
        ]}
        onReference={vi.fn()}
      />,
    );

    expect(html.match(/class="reference-item"/g)).toHaveLength(2);
    expect(html).toContain('href="https://one.example/news"');
    expect(html).toContain('href="https://two.example/news"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("falls back to the URL hostname and then the generic web label", () => {
    expect(referenceTitle({ type: "web", id: "https://news.example.com/a/very/long/path" }))
      .toBe("news.example.com");
    expect(referenceTitle({ type: "web", id: "not a valid URL" })).toBe("网页来源");
    expect(referenceTitle({ type: "web" })).toBe("网页来源");
  });

  it("does not render an empty reference area", () => {
    const html = renderToStaticMarkup(
      <ConversationReferences references={[]} onReference={vi.fn()} />,
    );

    expect(html).toBe("");
  });
});
