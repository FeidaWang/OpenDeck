#!/usr/bin/env node

import { cpSync, existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join, resolve, sep } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { stdin as input, stdout as output } from "node:process";
import { fileURLToPath } from "node:url";
import { startEditorServer } from "../lib/editor-server.js";

const SKILL_NAME = "open-kimi-ppt";
const MIN_NODE_MAJOR = 18;
const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceDirectory = join(packageRoot, "skills", SKILL_NAME);
const packageVersion = JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")).version;

const KNOWN_TARGETS = [
  { id: "agents", label: "Shared / default", relative: [".agents", "skills"] },
  { id: "codex", label: "Codex", relative: [".codex", "skills"] },
  { id: "claude", label: "Claude Code", relative: [".claude", "skills"] },
  { id: "cursor", label: "Cursor", relative: [".cursor", "skills"] },
  { id: "workbuddy", label: "WorkBuddy", relative: [".workbuddy", "skills"] },
];

function assertNodeVersion() {
  const major = Number.parseInt(process.versions.node.split(".")[0], 10);
  if (!Number.isInteger(major) || major < MIN_NODE_MAJOR) {
    throw new Error(
      `Node.js ${MIN_NODE_MAJOR}+ is required; found ${process.version}. Install from https://nodejs.org`,
    );
  }
}

function printHelp(command) {
  if (command === "codex") {
    console.log(`Plan and inspect a single-model Codex presentation workflow.

Usage (from the unpacked bundle):
  node bin/open-kimi-ppt-skill.js codex <command> [arguments]

Commands:
  doctor     Report local tools and supplied host capabilities
  plan       Build a single-model plan with the same quality gates at any effort
  init       Initialise a presentation workspace
  status     Show recorded progress
  record     Record a completed task and its output evidence
  validate   Validate source structure and resource paths
  layout     Measure and audit source layout

Use "codex <command> --help" for the Python workflow's exact arguments.
Requires Python 3.10+. Set OPEN_KIMI_PPT_PYTHON to an executable path if needed.
Help and doctor never install a skill or change Codex configuration.
`);
    return;
  }
  if (command === "serve") {
    console.log(`Start the local PPTD editor and exporter.

Usage:
  node bin/open-kimi-ppt-skill.js serve [options]

Options:
  --port <number>  HTTP port (default: 55173)
  --open           Open the editor in the default browser
  -h, --help       Show this help
  -V, --version    Show version
`);
    return;
  }

  console.log(`Install ${SKILL_NAME} for your AI coding agent or start its local editor.

Usage:
  node bin/open-kimi-ppt-skill.js [install] [options]
  node bin/open-kimi-ppt-skill.js serve [options]
  node bin/open-kimi-ppt-skill.js codex <command> [arguments]

Install options:
  --target <directory>  Skills directory (repeatable)
  -y, --yes             Non-interactive; install to ~/.agents/skills when no --target
  --all                 Install to all detected agent skill directories
                        (agents whose home directory is missing are skipped)
  --force               Accepted compatibility alias for explicit replacement
  -h, --help            Show this help
  -V, --version         Show version

In an interactive terminal (no --target / --yes / --all), a checklist is shown:
  ↑/↓ move  space select  a all  enter confirm

For agents / CI, prefer:
  node bin/open-kimi-ppt-skill.js install -y
  node bin/open-kimi-ppt-skill.js codex doctor

Use these commands from the unpacked bundle to keep this Codex edition.
Explicit install replaces an existing skill using staging and rollback.
Codex detection uses CODEX_HOME when set, otherwise ~/.codex.
Run "node bin/open-kimi-ppt-skill.js serve --help" for server options.
`);
}

function printVersion() {
  console.log(packageVersion);
}

function parseArguments(arguments_) {
  const args = [...arguments_];
  const command = ["install", "serve", "codex"].includes(args[0]) ? args.shift() : "install";
  if (command === "codex") {
    return {
      command,
      args,
      help: args.length === 0 || (args.length === 1 && ["--help", "-h"].includes(args[0])),
      version: args.length === 1 && ["--version", "-V"].includes(args[0]),
    };
  }
  const options = command === "serve"
    ? { command, open: false, port: 55173 }
    : { command, targets: [], yes: false, all: false };

  while (args.length > 0) {
    const argument = args.shift();

    // Accepted for backward compatibility; install always overwrites.
    if (command === "install" && argument === "--force") {
      continue;
    }

    if (command === "install" && (argument === "-y" || argument === "--yes")) {
      options.yes = true;
      continue;
    }

    if (command === "install" && argument === "--all") {
      options.all = true;
      continue;
    }

    if (command === "install" && argument === "--target") {
      const target = args.shift();
      if (!target || target.startsWith("-")) {
        throw new Error("--target requires a directory");
      }
      options.targets.push(resolve(target));
      continue;
    }

    if (command === "serve" && argument === "--port") {
      const port = Number(args.shift());
      if (!Number.isInteger(port) || port < 1 || port > 65_535) {
        throw new Error("--port must be an integer between 1 and 65535");
      }
      options.port = port;
      continue;
    }

    if (command === "serve" && argument === "--open") {
      options.open = true;
      continue;
    }

    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }

    if (argument === "--version" || argument === "-V") {
      options.version = true;
      continue;
    }

    throw new Error(`unknown argument: ${argument}`);
  }

  return options;
}

function openBrowser(url) {
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  const child = spawn(command, args, { detached: true, stdio: "ignore" });
  child.on("error", (error) => console.warn(`Could not open the browser: ${error.message}`));
  child.unref();
}

function defaultSkillsDirectory() {
  return join(homedir(), ".agents", "skills");
}

function knownTargetDirectories() {
  const home = homedir();
  return KNOWN_TARGETS.map((entry) => {
    const base = entry.id === "codex" && process.env.CODEX_HOME
      ? resolve(process.env.CODEX_HOME)
      : join(home, entry.relative[0]);
    return { ...entry, base, directory: join(base, ...entry.relative.slice(1)) };
  });
}

function displayPath(absolutePath) {
  const home = homedir();
  const normalized = absolutePath.split(sep).join("/");
  const homeNormalized = home.split(sep).join("/");
  if (normalized === homeNormalized || normalized.startsWith(`${homeNormalized}/`)) {
    return `~${normalized.slice(homeNormalized.length)}`;
  }
  return absolutePath;
}

function isInteractiveInstall() {
  return Boolean(input.isTTY && output.isTTY);
}

function uniqueDirectories(directories) {
  const seen = new Set();
  const unique = [];
  for (const directory of directories) {
    const key = resolve(directory);
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(key);
  }
  return unique;
}

async function promptInstallTargets() {
  const choices = knownTargetDirectories();
  const selected = new Set([0]);
  let cursor = 0;
  const lines = choices.length + 3;

  const render = (initial = false) => {
    if (!initial) {
      output.write(`\u001b[${lines}A`);
    }
    output.write("Install open-kimi-ppt to which skills directories?\n");
    output.write("↑/↓ move · space select · a all · enter confirm · ctrl+c cancel\n");
    for (const [index, choice] of choices.entries()) {
      const pointer = index === cursor ? "❯" : " ";
      const mark = selected.has(index) ? "◉" : "◯";
      const installed = existsSync(join(choice.directory, SKILL_NAME, "SKILL.md"))
        ? " (installed)"
        : "";
      const line = `${pointer}${mark} ${displayPath(choice.directory).padEnd(28)} ${choice.label}${installed}`;
      output.write(`\u001b[2K${line}\n`);
    }
  };

  return new Promise((resolvePromise, reject) => {
    if (typeof input.setRawMode !== "function") {
      resolvePromise([defaultSkillsDirectory()]);
      return;
    }

    render(true);
    input.setRawMode(true);
    input.resume();
    input.setEncoding("utf8");

    const cleanup = () => {
      input.off("data", onData);
      input.setRawMode(false);
      input.pause();
    };

    const onData = (key) => {
      if (key === "\u0003") {
        cleanup();
        output.write("\n");
        reject(new Error("install cancelled"));
        return;
      }

      if (key === "\u001b[A" || key === "k") {
        cursor = (cursor - 1 + choices.length) % choices.length;
        render();
        return;
      }

      if (key === "\u001b[B" || key === "j") {
        cursor = (cursor + 1) % choices.length;
        render();
        return;
      }

      if (key === " ") {
        if (selected.has(cursor)) selected.delete(cursor);
        else selected.add(cursor);
        render();
        return;
      }

      if (key === "a" || key === "A") {
        if (selected.size === choices.length) selected.clear();
        else for (let index = 0; index < choices.length; index += 1) selected.add(index);
        render();
        return;
      }

      if (key === "\r" || key === "\n") {
        if (selected.size === 0) {
          output.write("\u0007");
          return;
        }
        cleanup();
        output.write("\n");
        resolvePromise([...selected].sort((a, b) => a - b).map((index) => choices[index].directory));
      }
    };

    input.on("data", onData);
  });
}

function detectedTargetDirectories() {
  const detected = [];
  const skipped = [];
  for (const entry of knownTargetDirectories()) {
    if (existsSync(entry.base)) detected.push(entry);
    else skipped.push(entry);
  }
  return { detected, skipped };
}

async function resolveInstallTargets(options) {
  if (options.targets.length > 0) {
    return uniqueDirectories(options.targets);
  }

  if (options.all) {
    const { detected, skipped } = detectedTargetDirectories();
    for (const entry of skipped) {
      console.log(`Skipped ${entry.directory} (${entry.label} not found)`);
    }
    if (detected.length === 0) {
      console.warn(
        "No known agent directories found; nothing installed. Use -y for ~/.agents/skills or --target <directory>.",
      );
    }
    return uniqueDirectories(detected.map((entry) => entry.directory));
  }

  if (options.yes || !isInteractiveInstall()) {
    return [defaultSkillsDirectory()];
  }

  return promptInstallTargets();
}

function installSkillTo(skillsDirectory) {
  if (!existsSync(join(sourceDirectory, "SKILL.md"))) {
    throw new Error(`packaged skill is incomplete: ${sourceDirectory}`);
  }

  const destination = join(skillsDirectory, SKILL_NAME);
  mkdirSync(skillsDirectory, { recursive: true });
  const lock = join(skillsDirectory, `.${SKILL_NAME}.install-lock`);
  try {
    mkdirSync(lock);
  } catch (error) {
    if (error.code === "EEXIST") {
      throw new Error(`Installation is locked at ${lock}. Check for an active installer before removing a stale lock.`);
    }
    throw error;
  }
  const replaced = existsSync(destination);
  let stagingRoot;
  let preserveStaging = false;

  try {
    stagingRoot = mkdtempSync(join(skillsDirectory, `.${SKILL_NAME}-`));
    const stagedSkill = join(stagingRoot, SKILL_NAME);
    const backup = join(stagingRoot, "previous-installation");
    cpSync(sourceDirectory, stagedSkill, {
      recursive: true,
      filter: (source) => ![".DS_Store", "_user_meta.json", "__pycache__", ".pytest_cache"].includes(basename(source))
        && !/\.py[cod]$/.test(source),
    });

    if (replaced) renameSync(destination, backup);
    try {
      renameSync(stagedSkill, destination);
    } catch (error) {
      if (replaced) {
        try {
          renameSync(backup, destination);
        } catch (rollbackError) {
          preserveStaging = true;
          throw new Error(`Installation failed (${error.message}); rollback failed (${rollbackError.message}). Previous installation is preserved at ${backup}.`);
        }
      }
      throw error;
    }
  } finally {
    try {
      if (stagingRoot && !preserveStaging) rmSync(stagingRoot, { recursive: true, force: true });
    } finally {
      rmSync(lock, { recursive: true, force: true });
    }
  }

  console.log(
    replaced
      ? `Updated ${SKILL_NAME} at ${destination}`
      : `Installed ${SKILL_NAME} to ${destination}`,
  );
}

async function installSkill(options) {
  const targets = await resolveInstallTargets(options);
  for (const target of targets) {
    installSkillTo(target);
  }
}

function findPython() {
  const override = process.env.OPEN_KIMI_PPT_PYTHON;
  const candidates = override ? [override] : ["python3", "python"];
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"], {
      stdio: "ignore", timeout: 10_000, shell: false,
    });
    if (!probe.error && probe.status === 0) return candidate;
  }
  throw new Error("Python 3.10+ is required for the Codex workflow. Set OPEN_KIMI_PPT_PYTHON to a Python executable path. No installation was attempted.");
}

async function runCodex(args) {
  const script = join(sourceDirectory, "scripts", "codex_workflow.py");
  if (!existsSync(script)) throw new Error(`Packaged Codex workflow is missing: ${script}`);
  const python = findPython();
  const exitCode = await new Promise((resolvePromise, reject) => {
    const child = spawn(python, [script, ...args], { stdio: "inherit", shell: false });
    child.once("error", (error) => reject(new Error(`Could not start the Python workflow: ${error.message}`)));
    child.once("exit", (code, signal) => {
      if (signal) console.error(`Codex workflow ended with signal ${signal}.`);
      resolvePromise(code ?? 1);
    });
  });
  process.exitCode = exitCode;
}

async function main() {
  assertNodeVersion();
  const options = parseArguments(process.argv.slice(2));
  if (options.version) {
    printVersion();
    return;
  }
  if (options.help) {
    printHelp(options.command);
    return;
  }

  if (options.command === "install") {
    await installSkill(options);
    return;
  }
  if (options.command === "codex") {
    await runCodex(options.args);
    return;
  }

  const { server, url } = await startEditorServer({ port: options.port });
  console.log(`Open Kimi PPT editor is running at ${url}`);
  console.log("Press Ctrl+C to stop the server.");
  if (options.open) openBrowser(url);

  const shutdown = () => server.close(() => process.exit(0));
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
