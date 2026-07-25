import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const baselinePath = new URL("../.eslint-warning-baseline.json", import.meta.url);
const baseline = JSON.parse(await readFile(baselinePath, "utf8"));
const warningCeilings = baseline.rules;

const eslint = new ESLint({ cwd: frontendRoot });
const results = await eslint.lintFiles(["src/**/*.{js,jsx,ts,tsx}"]);
const errorCount = results.reduce((total, result) => total + result.errorCount, 0);
const warningsByRule = new Map();

for (const result of results) {
  for (const message of result.messages) {
    if (message.severity !== 1) continue;

    const ruleId = message.ruleId ?? "<unknown>";
    warningsByRule.set(ruleId, (warningsByRule.get(ruleId) ?? 0) + 1);
  }
}

const trackedRules = new Set([
  ...Object.keys(warningCeilings),
  ...warningsByRule.keys(),
]);
const overBudget = [];
const belowBudget = [];

for (const ruleId of [...trackedRules].sort()) {
  const count = warningsByRule.get(ruleId) ?? 0;
  const ceiling = warningCeilings[ruleId] ?? 0;

  if (count > ceiling) {
    overBudget.push({ ruleId, count, ceiling });
  } else if (count < ceiling) {
    belowBudget.push({ ruleId, count, ceiling });
  }
}

console.log("\nLint warning budget:");
for (const ruleId of Object.keys(warningCeilings).sort()) {
  const count = warningsByRule.get(ruleId) ?? 0;
  console.log(`  ${ruleId}: ${count}/${warningCeilings[ruleId]}`);
}

if (belowBudget.length > 0) {
  console.log("\nRatchet available (lower these ceilings in .eslint-warning-baseline.json):");
  for (const { ruleId, count, ceiling } of belowBudget) {
    console.log(`  ${ruleId}: ${ceiling} -> ${count}`);
  }
}

if (overBudget.length > 0) {
  console.error("\nLint warning budget exceeded:");
  for (const { ruleId, count, ceiling } of overBudget) {
    console.error(`  ${ruleId}: ${count} warnings (ceiling ${ceiling})`);
  }
}

if (errorCount > 0 || overBudget.length > 0) {
  const overBudgetRules = new Set(overBudget.map(({ ruleId }) => ruleId));
  const actionableResults = results
    .map((result) => {
      const messages = result.messages.filter(
        (message) =>
          message.severity === 2 ||
          (message.severity === 1 && overBudgetRules.has(message.ruleId ?? "<unknown>")),
      );

      return {
        ...result,
        messages,
        errorCount: messages.filter((message) => message.severity === 2).length,
        warningCount: messages.filter((message) => message.severity === 1).length,
        fixableErrorCount: messages.filter(
          (message) => message.severity === 2 && message.fix,
        ).length,
        fixableWarningCount: messages.filter(
          (message) => message.severity === 1 && message.fix,
        ).length,
      };
    })
    .filter((result) => result.messages.length > 0);
  const formatter = await eslint.loadFormatter("stylish");
  const formattedResults = formatter.format(actionableResults);

  if (formattedResults) {
    process.stderr.write(`\n${formattedResults}`);
  }

  process.exitCode = 1;
} else {
  console.log(`\nLint passed with 0 errors and ${[...warningsByRule.values()].reduce((sum, count) => sum + count, 0)} budgeted warnings.`);
}
