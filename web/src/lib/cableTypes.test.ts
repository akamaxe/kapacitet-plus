import { describe, expect, it } from "vitest";
import { namesOf, splitRow, withTranslations } from "./cableTypes";

describe("the translation of cable names as cable_types.csv", () => {
  it("writes a table of its own when the export has none", () => {
    expect(withTranslations(undefined, { "KABEL 150 AL": "AXQJ 4x150/41" })).toBe("name,type\nKABEL 150 AL,AXQJ 4x150/41\n");
  });

  it("leaves the export's table alone when nothing is chosen", () => {
    expect(withTranslations("name,type\nA,B\n", {})).toBe("name,type\nA,B\n");
    expect(withTranslations(undefined, { X: "" })).toBeUndefined();
  });

  it("adds to the export's own table in its separator and replaces a row of the same name", () => {
    const table = "name;type;r_ohm_per_km\r\nOLD;AXQJ 4x50/16;\r\nSAME;AXQJ 4x95/29;\r\n";
    expect(withTranslations(table, { SAME: "AXQJ 4x240/72", NEW: "N1XV 4x16" })).toBe(
      "name;type;r_ohm_per_km\nOLD;AXQJ 4x50/16;\nSAME;AXQJ 4x240/72;\nNEW;N1XV 4x16;\n",
    );
  });

  it("adds a type column to a table that gives only electrical data", () => {
    const table = "name,r_ohm_per_km,x_ohm_per_km,c_nf_per_km,max_i_ka\nOWN,0.2,0.08,0,0.24\n";
    expect(withTranslations(table, { NIS: "AXQJ 4x150/41" })).toBe(
      "name,r_ohm_per_km,x_ohm_per_km,c_nf_per_km,max_i_ka,type\nOWN,0.2,0.08,0,0.24,\nNIS,,,,,AXQJ 4x150/41\n",
    );
  });

  it("quotes a name with the separator in it, and reads quoted names back", () => {
    const out = withTranslations("name,type\n", { "N1XE 4G16, Cu": "N1XV 4x16" })!;
    expect(out).toBe('name,type\n"N1XE 4G16, Cu",N1XV 4x16\n');
    expect(splitRow('"N1XE 4G16, Cu",N1XV 4x16', ",")).toEqual(["N1XE 4G16, Cu", "N1XV 4x16"]);
    expect(withTranslations(out, { "N1XE 4G16, Cu": "AXQJ 4x50/16" })).toBe('name,type\n"N1XE 4G16, Cu",AXQJ 4x50/16\n');
  });

  it("leaves a table without a name column for the check to name", () => {
    expect(withTranslations("kabel,typ\nA,B\n", { A: "AXQJ 4x50/16" })).toBe("kabel,typ\nA,B\n");
  });

  it("reads the names of a finding and ignores anything else", () => {
    expect(namesOf({ names: [{ name: "A", count: 3 }, { name: 4 }, null] as unknown[] })).toEqual([{ name: "A", count: 3 }]);
    expect(namesOf({ types: "A (3)" })).toEqual([]);
    expect(namesOf(undefined)).toEqual([]);
  });
});
