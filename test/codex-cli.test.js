import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const cli = join(projectRoot, "bin", "opendeck.js");
const workflow = join(projectRoot, "skills", "opendeck", "scripts", "codex_workflow.py");

function runCli(args, options = {}) {
  return spawnSync(process.execPath, [cli, ...args], { encoding: "utf8", ...options });
}

function fakePython(root) {
  const executable = join(root, "python executable with spaces");
  writeFileSync(executable, `#!${process.execPath}
if (process.argv[2] === "-c") process.exit(0);
process.stdout.write(JSON.stringify({ args: process.argv.slice(2), cwd: process.cwd() }));
process.exit(Number(process.env.FAKE_PYTHON_EXIT || 0));
`);
  chmodSync(executable, 0o755);
  return executable;
}

test("codex forwards arguments as separate argv values without shell expansion", { skip: process.platform === "win32" }, () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-cli-"));
  try {
    const python = fakePython(root);
    const args = ["plan", "--brief", "brief with spaces.json", "--title", 'a "quoted" title; $(touch marker) `touch another`', "--", "-literal"];
    const result = runCli(["codex", ...args], {
      cwd: root,
      env: { ...process.env, OPENDECK_PYTHON: python },
    });
    assert.equal(result.status, 0, result.stderr);
    // The child reports its canonical cwd; on macOS tmpdir() is a symlink (/var -> /private/var).
    assert.deepEqual(JSON.parse(result.stdout), { args: [workflow, ...args], cwd: realpathSync(root) });
    assert.equal(existsSync(join(root, "marker")), false);
    assert.equal(existsSync(join(root, "another")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("codex preserves the workflow's failure exit status", { skip: process.platform === "win32" }, () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-cli-"));
  try {
    const result = runCli(["codex", "validate", "--project", "a project"], {
      env: { ...process.env, OPENDECK_PYTHON: fakePython(root), FAKE_PYTHON_EXIT: "7" },
    });
    assert.equal(result.status, 7, result.stderr);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("missing Python reports an explicit error and does not install anything", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-cli-"));
  try {
    const result = runCli(["codex", "doctor"], {
      cwd: root,
      env: { ...process.env, HOME: root, USERPROFILE: root, OPENDECK_PYTHON: join(root, "missing-python") },
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Python 3\.10\+/);
    assert.match(result.stderr, /OPENDECK_PYTHON/);
    assert.match(result.stderr, /No installation was attempted/);
    assert.deepEqual(readdirSync(root), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("top-level and codex help work without Python and leave the home directory untouched", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-cli-"));
  try {
    for (const args of [["--help"], ["install", "--help"], ["codex"], ["codex", "--help"]]) {
      const result = runCli(args, {
        cwd: root,
        env: { ...process.env, HOME: root, USERPROFILE: root, OPENDECK_PYTHON: join(root, "missing-python") },
      });
      assert.equal(result.status, 0, result.stderr);
      assert.match(result.stdout, /Usage/);
      assert.doesNotMatch(result.stdout, /npx.*@latest/);
    }
    assert.deepEqual(readdirSync(root), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("doctor dispatch leaves existing skill and Codex configuration unchanged", { skip: process.platform === "win32" }, () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-cli-"));
  try {
    const skill = join(root, ".agents", "skills", "opendeck", "SKILL.md");
    const config = join(root, "custom codex", "config.toml");
    mkdirSync(dirname(skill), { recursive: true });
    mkdirSync(dirname(config), { recursive: true });
    writeFileSync(skill, "existing custom skill");
    writeFileSync(config, 'model = "existing-model"\n');
    const result = runCli(["codex", "doctor"], {
      cwd: root,
      env: { ...process.env, HOME: root, USERPROFILE: root, CODEX_HOME: dirname(config), OPENDECK_PYTHON: fakePython(root) },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout).args, [workflow, "doctor"]);
    assert.equal(readFileSync(skill, "utf8"), "existing custom skill");
    assert.equal(readFileSync(config, "utf8"), 'model = "existing-model"\n');
    assert.equal(existsSync(join(dirname(config), "skills")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("portable plugin metadata preserves identity and uses fixed skill discovery", () => {
  const pkg = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));
  const plugin = JSON.parse(readFileSync(join(projectRoot, "plugin.json"), "utf8"));
  assert.equal(pkg.name, "opendeck");
  assert.equal(plugin.name, pkg.name);
  assert.match(pkg.version, /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/);
  assert.equal(plugin.version, pkg.version);
  assert.equal(plugin.$schema, "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json");
  const presentation = plugin.extensions["com.openai"].interface;
  assert.ok(presentation.shortDescription.length <= 30);
  for (const field of ["skills", "interface", "apps", "mcpServers"]) assert.equal(field in plugin, false);
  assert.equal("apps" in plugin.extensions["com.openai"], false);
  assert.equal(existsSync(join(projectRoot, "skills", "opendeck", "SKILL.md")), true);
});

test("npm packaging includes all workflow runtime scripts, config, agents and references", () => {
  const root = mkdtempSync(join(tmpdir(), "opendeck-pack-"));
  try {
    const result = spawnSync("npm", ["pack", "--dry-run", "--json", "--ignore-scripts", "--offline", "--cache", join(root, "cache")], {
      cwd: projectRoot, encoding: "utf8", timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    const [packed] = JSON.parse(result.stdout);
    const paths = new Set(packed.files.map((file) => file.path));
    for (const required of ["plugin.json", "skills/opendeck/SKILL.md", "skills/opendeck/scripts/codex_workflow.py", "skills/opendeck/scripts/asset_audit.py", "skills/opendeck/config/codex-profiles.json"]) {
      assert.ok(paths.has(required), `Missing from npm package: ${required}`);
    }
    const ignored = new Set(["__pycache__", ".pytest_cache", ".DS_Store", "_user_meta.json"]);
    const checkTree = (directory) => {
      if (!existsSync(directory)) return;
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        if (ignored.has(entry.name) || /\.py[cod]$/.test(entry.name)) continue;
        const path = join(directory, entry.name);
        if (entry.isDirectory()) checkTree(path);
        else if (entry.isFile()) {
          const packedPath = relative(projectRoot, path).split("\\").join("/");
          assert.ok(paths.has(packedPath), `Runtime file missing from npm package: ${packedPath}`);
        }
      }
    };
    for (const directory of ["scripts", "config", "agents", "reference", "references"]) {
      checkTree(join(projectRoot, "skills", "opendeck", directory));
    }
    assert.equal([...paths].some((path) => /(^|\/)__pycache__\/|\.py[cod]$/.test(path)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
