import { describe, expect, it } from "vitest";
import { messageUrlTransform, parseArtifactLink } from "./artifact-links";

const id = "802b4e3a-0b84-4906-9964-324534dacbd9";
describe("artifact links", () => {
  it("recognizes preview and download links and rejects malformed ids", () => {
    expect(parseArtifactLink(`artifact://${id}`)).toEqual({ id, action: "preview" });
    expect(parseArtifactLink(`artifact-download://${id}`)).toEqual({ id, action: "download" });
    expect(parseArtifactLink("artifact://../secret")).toBeNull();
  });
  it("allows only safe message destinations", () => {
    expect(messageUrlTransform("opencore-studio://music")).toBe("opencore-studio://music");
    expect(messageUrlTransform("opencore-studio://3d")).toBe("opencore-studio://3d");
    expect(messageUrlTransform("opencore-studio://../secret")).toBe("");
    expect(messageUrlTransform("opencore-studio://music?url=https://other.example")).toBe("");
    expect(messageUrlTransform(`artifact://${id}`)).toBe(`artifact://${id}`);
    expect(messageUrlTransform("https://example.com/image.png")).toContain("https://");
    expect(messageUrlTransform("javascript:alert(1)")).toBe("");
    expect(messageUrlTransform("file:///C:/notes.txt")).toBe("file:///C:/notes.txt");
    expect(messageUrlTransform("/C:/project/research.md:12")).toBe("/C:/project/research.md:12");
    expect(messageUrlTransform("./research.md")).toBe("");
  });
});
