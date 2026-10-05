import { describe, expect, it } from "vitest";
import { availableComposerSkills, filterComposerSkills, resolveSlashSkill, exactSlashSkill } from "./composer-skills";

describe("composer skills", () => {
  it('keeps development skills available without a media model', () => {
    const ids = availableComposerSkills([]).map(skill => skill.id);
    for (const id of ['game-dev', 'web-dev', 'full-stack', 'mobile-dev', 'desktop-dev', 'mcp-server', 'plugins', 'skills-library']) {
      expect(ids).toContain(id);
      expect(exactSlashSkill(`/${id} Build the project`)?.id).toBe(id);
    }
  });
  it.each(['video', 'tts', 'voice-cloning', 'ocr', 'omni', 'policy'])('gates /%s on installed weights or a connected runtime', (category) => {
    expect(filterComposerSkills(`/${category}`, [])).toEqual([]);
    expect(filterComposerSkills(`/${category}`, [{ category, installed: false }])).toEqual([]);
    expect(filterComposerSkills(`/${category}`, [{ category, installed: false, runtimeConnected: true }]).map(skill => skill.id)).toEqual([category]);
    expect(filterComposerSkills(`/${category}`, [{ category, installed: true }]).map(skill => skill.id)).toEqual([category]);
  });
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
