import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const cli = join(projectRoot, "bin", "opendeck.js");

function runCli(args, env = process.env, nodeArgs = []) {
  return spawnSync(process.execPath, [...nodeArgs, cli, ...args], {
    encoding: "utf8",
    env,
  });
}

test("installs the packaged skill into a custom skills directory", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");

  try {
    const result = runCli(["install", "--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(target, "opendeck", "SKILL.md")), true);
    assert.equal(existsSync(join(target, "opendeck", "scripts", "pptd_project.py")), true);
    for (const removed of ["export_pptx.py", "export_images.py", "export_host.html"]) {
      assert.equal(existsSync(join(target, "opendeck", "scripts", removed)), false);
    }
    assert.equal(existsSync(join(target, "opendeck", "scripts", "codex_workflow.py")), true);
    assert.equal(existsSync(join(target, "opendeck", "config", "codex-profiles.json")), true);
    assert.equal(existsSync(join(target, "opendeck", "_user_meta.json")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("installs into ~/.agents/skills when no target is provided (non-interactive)", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));

  try {
    const result = runCli([], { ...process.env, HOME: root, USERPROFILE: root, CODEX_HOME: undefined });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents", "skills", "opendeck", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("supports -y for non-interactive default install", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));

  try {
    const result = runCli(["install", "-y"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents", "skills", "opendeck", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("installs into multiple --target directories", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const first = join(root, "codex", "skills");
  const second = join(root, "claude", "skills");

  try {
    const result = runCli(["install", "--target", first, "--target", second]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(first, "opendeck", "SKILL.md")), true);
    assert.equal(existsSync(join(second, "opendeck", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--all installs into detected agent directories and skips missing ones", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));

  try {
    mkdirSync(join(root, ".agents"), { recursive: true });
    mkdirSync(join(root, ".codex"), { recursive: true });
    mkdirSync(join(root, ".claude"), { recursive: true });

    const result = runCli(["install", "--all"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.equal(result.status, 0, result.stderr);

    for (const relative of [[".agents", "skills"], [".codex", "skills"], [".claude", "skills"]]) {
      assert.equal(
        existsSync(join(root, ...relative, "opendeck", "SKILL.md")),
        true,
        relative.join("/"),
      );
    }
    for (const missing of [".cursor", ".workbuddy"]) {
      assert.equal(existsSync(join(root, missing)), false, missing);
    }
    assert.match(result.stdout, /Skipped .*\.cursor/);
    assert.match(result.stdout, /Skipped .*\.workbuddy/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--all warns and installs nothing when no agent directory exists", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));

  try {
    const result = runCli(["install", "--all"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents")), false);
    assert.match(result.stderr, /No known agent directories found/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--all detects Codex at CODEX_HOME without creating ~/.codex", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const home = join(root, "home");
  const codexHome = join(root, "custom codex home");
  try {
    mkdirSync(home);
    mkdirSync(codexHome);
    writeFileSync(join(codexHome, "config.toml"), 'model = "user-selected"\n');
    const result = runCli(["install", "--all"], {
      ...process.env, HOME: home, USERPROFILE: home, CODEX_HOME: codexHome,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(codexHome, "skills", "opendeck", "SKILL.md")), true);
    assert.equal(existsSync(join(home, ".codex")), false);
    assert.equal(readFileSync(join(codexHome, "config.toml"), "utf8"), 'model = "user-selected"\n');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("overwrites an existing installation by default", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");
  const skillFile = join(target, "opendeck", "SKILL.md");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(skillFile, "modified", "utf8");

    const result = runCli(["--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Updated opendeck/);
    assert.match(readFileSync(skillFile, "utf8"), /^---\nname: opendeck/m);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("accepts legacy --force without changing overwrite behavior", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    const result = runCli(["--target", target, "--force"]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Updated opendeck/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("restores the previous installation if publishing the staged copy fails", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");
  const skillFile = join(target, "opendeck", "SKILL.md");
  const hook = join(root, "fail-publish.cjs");
  try {
    assert.equal(runCli(["install", "--target", target]).status, 0);
    writeFileSync(skillFile, "original user installation", "utf8");
    writeFileSync(hook, `
const fs = require("node:fs");
const path = require("node:path");
const rename = fs.renameSync;
fs.renameSync = (from, to) => {
  if (path.basename(from) === "opendeck" && path.basename(path.dirname(from)).startsWith(".opendeck-")) {
    throw new Error("simulated publish failure");
  }
  return rename(from, to);
};
require("node:module").syncBuiltinESMExports();
`);
    const result = runCli(["install", "--target", target], process.env, ["--require", hook]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /simulated publish failure/);
    assert.equal(readFileSync(skillFile, "utf8"), "original user installation");
    assert.deepEqual(readdirSync(target), ["opendeck"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preserves the previous copy and reports its path if rollback also fails", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");
  const hook = join(root, "fail-rollback.cjs");
  try {
    assert.equal(runCli(["install", "--target", target]).status, 0);
    writeFileSync(join(target, "opendeck", "SKILL.md"), "irreplaceable user copy");
    writeFileSync(hook, `
const fs = require("node:fs");
const path = require("node:path");
const rename = fs.renameSync;
fs.renameSync = (from, to) => {
  if (path.basename(path.dirname(from)).startsWith(".opendeck-")) {
    throw new Error("simulated rename failure");
  }
  return rename(from, to);
};
require("node:module").syncBuiltinESMExports();
`);
    const result = runCli(["install", "--target", target], process.env, ["--require", hook]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Previous installation is preserved at/);
    const staging = readdirSync(target).find((name) => name.startsWith(".opendeck-"));
    assert.ok(staging);
    assert.equal(readFileSync(join(target, staging, "previous-installation", "SKILL.md"), "utf8"), "irreplaceable user copy");
    assert.equal(existsSync(join(target, ".opendeck.install-lock")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("an active install lock leaves the existing skill untouched", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-test-"));
  const target = join(root, "skills");
  try {
    assert.equal(runCli(["install", "--target", target]).status, 0);
    const skillFile = join(target, "opendeck", "SKILL.md");
    writeFileSync(skillFile, "keep existing");
    mkdirSync(join(target, ".opendeck.install-lock"));
    const result = runCli(["install", "--target", target]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Installation is locked/);
    assert.equal(readFileSync(skillFile, "utf8"), "keep existing");
    assert.equal(existsSync(join(target, ".opendeck.install-lock")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("help documents interactive install and -y for agents", () => {
  const result = runCli(["install", "--help"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /space select/);
  assert.match(result.stdout, /-y, --yes/);
  assert.match(result.stdout, /--all/);
  assert.match(result.stdout, /node bin\/opendeck\.js install -y/);
  assert.doesNotMatch(result.stdout, /npx.*@latest/);
  assert.match(result.stdout, /CODEX_HOME/);
});

test("-h shows help", () => {
  const result = runCli(["-h"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Usage:/);
  assert.match(result.stdout, /-h, --help/);
  assert.match(result.stdout, /-V, --version/);
});

test("--version and -V print the package version", () => {
  const { version } = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));
  for (const args of [["--version"], ["-V"], ["codex", "--version"]]) {
    const result = runCli(args);
    assert.equal(result.status, 0, `${args.join(" ")}: ${result.stderr}`);
    assert.equal(result.stdout.trim(), version);
  }
});
