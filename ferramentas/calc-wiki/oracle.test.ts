/**
 * Runs inside a checkout of weirdgloop/osrs-dps-calc (GPL-3.0) to:
 *  - export the lookup tables used by the Python port (EXPORT_OUT), and
 *  - compute reference DPS values for the Python tests (ORACLE_IN -> ORACLE_OUT).
 *
 * See ferramentas/calc-wiki/rodar.sh.
 */
import { describe, expect, test } from '@jest/globals';
import fs from 'fs';
import { EquipmentCategory } from '@/enums/EquipmentCategory';
import { Prayer } from '@/enums/Prayer';
import {
  AmmoApplicability, ammoApplicability, availableEquipment, equipmentAliases,
} from '@/lib/Equipment';
import { BLOWPIPE_IDS } from '@/lib/constants';
import { getCombatStylesForCategory } from '@/utils';
import { PlayerCombatStyle } from '@/types/PlayerCombatStyle';
import {
  calculatePlayerVsNpc, findEquipmentById, getTestMonster, getTestPlayer,
} from '@/tests/utils/TestUtils';

const RANGED_CATEGORIES = [
  EquipmentCategory.BOW,
  EquipmentCategory.CROSSBOW,
  EquipmentCategory.THROWN,
  EquipmentCategory.CHINCHOMPA,
  EquipmentCategory.SALAMANDER,
];

interface OracleCase {
  id: string;
  monster: { name: string; version: string };
  currentHp: number;
  equipment: { [slot: string]: number };
  dart?: number;
  style: PlayerCombatStyle;
  prayers: string[];
  boosts: { [skill: string]: number };
  skills?: { [skill: string]: number };
  onSlayerTask: boolean;
}

describe('calc-wiki', () => {
  test('export tables', () => {
    const out = process.env.EXPORT_OUT;
    if (!out) return;

    const styles: { [category: string]: PlayerCombatStyle[] } = {};
    Object.values(EquipmentCategory).forEach((category) => {
      styles[category] = getCombatStylesForCategory(category);
    });

    const ammoItems = availableEquipment.filter((e) => e.slot === 'ammo');
    const ammo: { [weaponId: number]: number[] } = {};
    availableEquipment
      .filter((e) => e.slot === 'weapon' && RANGED_CATEGORIES.includes(e.category))
      .forEach((weapon) => {
        if (ammoApplicability(weapon.id, undefined) === AmmoApplicability.ALLOWED) return;
        ammo[weapon.id] = ammoItems
          .filter((a) => ammoApplicability(weapon.id, a.id) === AmmoApplicability.INCLUDED)
          .map((a) => a.id);
      });

    fs.writeFileSync(out, JSON.stringify({
      styles, ammo, aliases: equipmentAliases, blowpipeIds: BLOWPIPE_IDS,
    }));
    expect(Object.keys(ammo).length).toBeGreaterThan(0);
  });

  test('oracle', () => {
    const input = process.env.ORACLE_IN;
    const out = process.env.ORACLE_OUT;
    if (!input || !out) return;

    const cases: OracleCase[] = JSON.parse(fs.readFileSync(input, 'utf-8'));
    const results = cases.map((c) => {
      const monster = getTestMonster(c.monster.name, c.monster.version);
      monster.inputs.monsterCurrentHp = c.currentHp;

      const equipment: { [slot: string]: unknown } = {};
      Object.entries(c.equipment).forEach(([slot, id]) => {
        equipment[slot] = findEquipmentById(id);
      });
      if (c.dart !== undefined && equipment.weapon) {
        equipment.weapon = { ...(equipment.weapon as object), itemVars: { blowpipeDartId: c.dart } };
      }

      const player = getTestPlayer(monster, {
        equipment,
        style: c.style,
        prayers: c.prayers.map((p) => Prayer[p as keyof typeof Prayer]),
        boosts: c.boosts,
        skills: c.skills,
        buffs: { onSlayerTask: c.onSlayerTask },
      } as never);
      const result = calculatePlayerVsNpc(monster, player);
      return {
        id: c.id,
        accuracy: result.accuracy,
        maxHit: result.maxHit,
        maxAttackRoll: result.maxAttackRoll,
        npcDefRoll: result.npcDefRoll,
        attackSpeed: player.attackSpeed,
        expectedHit: result.dist.getExpectedDamage(),
        dps: result.dps,
      };
    });

    fs.writeFileSync(out, JSON.stringify(results, null, 1));
    expect(results.length).toBe(cases.length);
  });
});
