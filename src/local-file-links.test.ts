import { describe, expect, it } from "vitest";
import { localFilePath } from "./local-file-links";

describe("local file destinations", () => {
  it("opens historical Windows source links, including escaped spaces and line numbers", () => {
    expect(localFilePath("</C:/project/frozen%20research.md:12>")).toBe("C:/project/frozen research.md");
    expect(localFilePath("file:///C:/project/trainer.py#L20")).toBe("C:/project/trainer.py");
    expect(localFilePath("C:\\project\\trainer.py:20:4")).toBe("C:\\project\\trainer.py");
    expect(localFilePath("/C:/project/החלטות.md")).toBe("C:/project/החלטות.md");
  });
  it("rejects navigation URLs, device targets, alternate streams and malformed paths", () => {
    for (const value of ["", "../trainer.py", "javascript:alert(1)", "https://example.com/C:/file", "file://server/share/file", "\\\\.\\device", "C:/notes.txt:secret", "C:/bad%00file", "C:/bad%XXfile"])
      expect(localFilePath(value)).toBeNull();
  });
});
