import { describe, expect, it } from "vitest";
import { filterComposerSkills, resolveSlashSkill } from "./composer-skills";

describe("composer skills", () => {
  it("finds the two computer skills from slash input", () => {
    expect(filterComposerSkills("/computer").map((skill) => skill.id)).toEqual(["computer-use"]);
    expect(filterComposerSkills("/chrome").map((skill) => skill.id)).toEqual(["chrome-control"]);
    expect(filterComposerSkills("hello /computer")).toEqual([]);
  });

  it("removes the slash token and preserves the task", () => {
    expect(resolveSlashSkill("/computer-use open Calculator", "computer-use"))
      .toBe("open Calculator");
    expect(resolveSlashSkill("/chrome", "chrome-control")).toBe("");
  });
});
