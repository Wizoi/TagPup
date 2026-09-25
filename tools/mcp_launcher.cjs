/**
 * Starts TagPup's MCP server, `python -m tagpup.mcp`, for Claude Code, whatever folder
 * Claude Code starts it from (docs/findings.md, #173).
 *
 * .mcp.json named `.venv/Scripts/python.exe`, a path relative to the folder the server
 * was started in: a session started anywhere but the checkout's root, or in a worktree,
 * which has no .venv of its own, had no server. Claude Code expands ${VAR} in .mcp.json
 * from its own environment, which does not hold CLAUDE_PROJECT_DIR; it gives
 * CLAUDE_PROJECT_DIR to the server's process instead. So .mcp.json runs `node` from
 * PATH, whose one-line script finds this file from CLAUDE_PROJECT_DIR or, without it,
 * from the working folder and the folders above it; and this file finds the rest from
 * its own path:
 *
 * - the checkout is the folder above tools/, and the server runs its code, there;
 * - the interpreter is the checkout's .venv, else, in a git worktree, the main
 *   checkout's (`git rev-parse --git-common-dir`).
 *
 * With TAGPUP_MCP_WHICH set it prints the interpreter and the checkout, one a line, and
 * starts nothing: what tests/test_mcp_config_resolves.py asks.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync, spawn } = require('child_process');

const CHECKOUT = path.dirname(__dirname);
const VENV_PYTHON = process.platform === 'win32'
    ? ['.venv', 'Scripts', 'python.exe']
    : ['.venv', 'bin', 'python'];

function pythonIn(folder) {
    const found = path.join(folder, ...VENV_PYTHON);
    return fs.existsSync(found) ? found : null;
}

/** The main checkout of the git worktree `folder` is in, or null. */
function mainCheckout(folder) {
    try {
        const common = execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], {
            cwd: folder, encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'],
        }).trim();
        return common ? path.dirname(path.resolve(common)) : null;
    } catch {
        return null;
    }
}

function interpreter() {
    const own = pythonIn(CHECKOUT);
    if (own) return own;
    const main = mainCheckout(CHECKOUT);
    return main ? pythonIn(main) : null;
}

const python = interpreter();
if (!python) {
    process.stderr.write(`TagPup's MCP server: no .venv beside ${CHECKOUT} or its main checkout. Run setup.bat.\n`);
    process.exit(1);
}
if (process.env.TAGPUP_MCP_WHICH) {
    process.stdout.write(python + '\n' + CHECKOUT + '\n');
    process.exit(0);
}
const child = spawn(python, ['-m', 'tagpup.mcp'], { cwd: CHECKOUT, stdio: 'inherit', windowsHide: true });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', (error) => {
    process.stderr.write(`TagPup's MCP server could not start: ${error.message}\n`);
    process.exit(1);
});
child.on('exit', (code) => process.exit(code === null ? 1 : code));
