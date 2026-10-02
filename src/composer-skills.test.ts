import { describe, expect, it } from "vitest";
import { filterComposerSkills, resolveSlashSkill, exactSlashSkill } from "./composer-skills";

describe("composer skills", () => {
  it('recognizes an exact slash command so clicking Send enables the skill',()=>{
    expect(exactSlashSkill('/music Make a song about AI')?.id).toBe('music');
    expect(exactSlashSkill('/3d Make a robot')?.id).toBe('3d');
    expect(exactSlashSkill('/3d-animation Make it dance')?.id).toBe('3d-animation');
    expect(exactSlashSkill('/m Make a song')).toBeUndefined();
  });
  it("unlocks category skills only while a model in that category is installed", () => {
    expect(filterComposerSkills('/music', [])).toEqual([]);
    expect(filterComposerSkills('/music', [{ category: 'music', installed: false }])).toEqual([]);
    expect(filterComposerSkills('/music', [{ category: 'music', installed: true }]).map(skill => skill.id)).toEqual(['music']);
    expect(filterComposerSkills('/3d', [{ category: '3d', installed: true }]).map(skill => skill.id)).toEqual(['3d']);
    expect(filterComposerSkills('/3d', [{ category: '3d-animation', installed: true }]).map(skill => skill.id)).toEqual(['3d-animation']);
  });
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
