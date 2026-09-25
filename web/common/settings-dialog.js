/**
 * The library's settings, from the gear (docs/ARCHITECTURE.md, phase 7.6).
 *
 * The settings a library depends on -- the CLIP model its vectors were made with, face
 * detection, Suggest's candidate words, the rename format and the ExifTool program --
 * lived in config.ini, which nothing in the pages showed. Each library holds its own
 * now, and this dialog shows and changes them. It is made entirely from what the server
 * says of each setting (/api/settings: its label, type, value, default, info text,
 * whether it is locked and what changing it does -- tagpup.core.validation.SETTINGS),
 * so a setting declared there appears here without a line of this file changing. Each
 * value is checked as it is typed against the rules /api/rules publishes (validate.js),
 * and the server checks it again before it writes.
 *
 * A setting whose change has consequences is locked: shown, not editable. Its group
 * opens only through Change..., which lists each consequence with a box to tick; Save
 * is enabled only once every box of every group opened is ticked. A change is saved as
 * one change of the library's journal, which can be undone (tagpup_cli.py undo), and
 * after a locked one is saved the page reloads, so nothing it shows was made with the
 * old values. Candidate words and the rename format are edited directly and apply from
 * the next run.
 *
 * The dialog is a TagTuner `.modal`, so the pages' shortcuts leave it alone while it is
 * open (web/common/dialog.js).
 */
import { api } from './api.js';
import { buildElement, replaceContent } from './dom.js';
import { loadRules, ruleProblem } from './validate.js';

const settingsDialog = {
    modal: null,
    title: null,
    body: null,
    status: null,
    save: null,
    /** What /api/settings answered, last. */
    data: null,
    /** key -> { setting, input, problem } */
    fields: new Map(),
    /** The locked groups opened through Change..., by name -> their consequence boxes. */
    opened: new Map(),
    opener: null,
    saving: false,
};

const TRUE_WORDS = ['true', 'yes', 'on', '1'];

/** A setting's value as the input shows it, and back: a boolean as a checkbox. */
function inputValue(setting, input) {
    if (setting.type === 'boolean') return input.checked ? 'true' : 'false';
    return input.value;
}

function shownValue(setting) {
    if (setting.type === 'boolean') {
        return TRUE_WORDS.includes(String(setting.value).trim().toLowerCase()) ? 'true' : 'false';
    }
    return setting.value;
}

function say(text) {
    if (settingsDialog.status) settingsDialog.status.textContent = text || '';
}

/** The settings whose value the dialog now holds differently from the library: key -> value. */
function changedValues() {
    const changed = {};
    for (const [key, field] of settingsDialog.fields) {
        const now = inputValue(field.setting, field.input);
        if (now.trim() !== String(shownValue(field.setting)).trim()) changed[key] = now;
    }
    return changed;
}

/** Why Save cannot be pressed now, or '' when it can. */
function whyNotSave() {
    let problems = 0;
    for (const [, field] of settingsDialog.fields) {
        const problem = ruleProblem(field.setting.kind, inputValue(field.setting, field.input));
        field.problem.textContent = problem || '';
        field.problem.classList.toggle('hidden', !problem);
        if (problem) problems += 1;
    }
    if (problems) return 'Correct the values marked above.';
    for (const [, boxes] of settingsDialog.opened) {
        if (boxes.some(box => !box.checked)) return 'Tick each consequence to change a locked setting.';
    }
    if (!Object.keys(changedValues()).length) return '';
    return null;
}

/** Enable Save when something changed, every value may be set, and every consequence is ticked. */
function refreshSave() {
    const why = whyNotSave();
    settingsDialog.save.disabled = why !== null || settingsDialog.saving;
    settingsDialog.save.title = why || 'Save the changed settings to this library';
}

function lockGroup(group, locked) {
    for (const setting of group.settings) {
        const field = settingsDialog.fields.get(setting.key);
        if (!field) continue;
        if (setting.type === 'boolean') field.input.disabled = locked;
        else field.input.readOnly = locked;
        field.input.setAttribute('aria-readonly', locked ? 'true' : 'false');
    }
}

/** Change...: the group's fields become editable, and its consequences are listed to tick. */
function openLockedGroup(group, section, button) {
    if (settingsDialog.opened.has(group.name)) return;
    const boxes = [];
    const list = buildElement('ul', { className: 'settings-consequences' });
    group.consequences.forEach((text, i) => {
        const box = buildElement('input', {
            attrs: { type: 'checkbox', id: `settings-${group.name}-consequence-${i}` },
            data: { consequence: String(i) },
        });
        box.addEventListener('change', refreshSave);
        boxes.push(box);
        list.append(buildElement('li', {}, [
            buildElement('label', { attrs: { for: box.id } }, [box, buildElement('span', { text })]),
        ]));
    });
    const notice = buildElement('div', { className: 'settings-change-notice', attrs: { role: 'group' } }, [
        buildElement('p', { text: `Changing ${group.title.toLowerCase()} means:` }),
        list,
    ]);
    button.after(notice);
    button.disabled = true;
    settingsDialog.opened.set(group.name, boxes);
    lockGroup(group, false);
    const first = group.settings.map(s => settingsDialog.fields.get(s.key)).find(Boolean);
    if (first) first.input.focus();
    section.classList.add('settings-group-open');
    refreshSave();
}

function settingRow(setting) {
    const id = `setting-${setting.key.replace(/[^A-Za-z0-9_-]/g, '-')}`;
    let input;
    if (setting.type === 'boolean') {
        input = buildElement('input', { id, attrs: { type: 'checkbox' } });
        input.checked = shownValue(setting) === 'true';
    } else if (setting.key === 'candidates.tags') {
        input = buildElement('textarea', { id, className: 'modal-input', attrs: { rows: 3 } });
        input.value = setting.value;
    } else {
        input = buildElement('input', { id, className: 'modal-input', attrs: { type: 'text', spellcheck: 'false' } });
        input.value = setting.value;
        if (setting.key === 'paths.exiftool' && settingsDialog.data && settingsDialog.data.exiftool_found) {
            input.placeholder = `Found: ${settingsDialog.data.exiftool_found}`;
        }
    }
    input.dataset.setting = setting.key;
    const infoId = `${id}-info`;
    const info = buildElement('p', { id: infoId, className: 'settings-info hidden', text: setting.info });
    const infoButton = buildElement('button', {
        className: 'settings-info-button',
        text: 'i',
        title: `About ${setting.label}`,
        attrs: { type: 'button', 'aria-label': `About ${setting.label}`, 'aria-expanded': 'false', 'aria-controls': infoId },
        data: { info: setting.key },
    });
    infoButton.addEventListener('click', () => {
        const showing = info.classList.toggle('hidden') === false;
        infoButton.setAttribute('aria-expanded', showing ? 'true' : 'false');
    });
    const problem = buildElement('div', { className: 'validation-error hidden', attrs: { role: 'alert' } });
    input.addEventListener('input', refreshSave);
    input.addEventListener('change', refreshSave);
    settingsDialog.fields.set(setting.key, { setting, input, problem });
    return buildElement('div', { className: 'settings-row form-group', data: { key: setting.key } }, [
        buildElement('div', { className: 'settings-label' }, [
            buildElement('label', { text: setting.label, attrs: { for: id } }),
            infoButton,
        ]),
        input,
        problem,
        info,
    ]);
}

function groupSection(group) {
    const section = buildElement('section', { className: 'settings-group', data: { group: group.name } }, [
        buildElement('h4', { text: group.title }),
    ]);
    for (const setting of group.settings) section.append(settingRow(setting));
    if (group.locked) {
        section.classList.add('settings-group-locked');
        lockGroup(group, true);
        const button = buildElement('button', {
            className: 'btn btn-secondary btn-sm settings-change',
            text: 'Change...',
            title: `Change ${group.title.toLowerCase()}: see what it means first`,
            attrs: { type: 'button' },
            data: { group: group.name },
        });
        button.addEventListener('click', () => openLockedGroup(group, section, button));
        section.append(button);
    }
    return section;
}

function renderSettings(data) {
    settingsDialog.data = data;
    settingsDialog.fields = new Map();
    settingsDialog.opened = new Map();
    settingsDialog.title.textContent = `Library settings: ${data.library}`;
    replaceContent(settingsDialog.body, ...data.groups.map(groupSection));
    say('');
    refreshSave();
}

/** The dialog's frame, built once and put at the end of the page's body. */
function buildSettingsDialog() {
    // The multiplication sign, by its code: no escape in this file can be mangled in transit.
    const close = buildElement('button', { className: 'close-btn', text: String.fromCharCode(0xd7), title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    const cancel = buildElement('button', { className: 'btn btn-secondary', text: 'Cancel', attrs: { type: 'button' } });
    const save = buildElement('button', { id: 'btn-settings-save', className: 'btn btn-primary', text: 'Save',
        attrs: { type: 'button', disabled: true } });
    settingsDialog.title = buildElement('h3', { id: 'settings-title', text: 'Library settings' });
    settingsDialog.body = buildElement('div', { className: 'modal-body settings-body' });
    settingsDialog.status = buildElement('span', { className: 'settings-status', attrs: { role: 'status' } });
    settingsDialog.save = save;
    const modal = buildElement('div', {
        id: 'settings-modal',
        className: 'modal settings-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'settings-title' },
    }, [
        buildElement('div', { className: 'modal-content settings-content' }, [
            buildElement('div', { className: 'modal-header' }, [settingsDialog.title, close]),
            settingsDialog.body,
            buildElement('div', { className: 'modal-footer settings-footer' }, [settingsDialog.status, cancel, save]),
        ]),
    ]);
    document.body.append(modal);
    close.addEventListener('click', closeSettings);
    cancel.addEventListener('click', closeSettings);
    save.addEventListener('click', saveSettings);
    // Escape closes it wherever the focus is: a Save that disables itself after saving
    // drops the focus to the page's body, where a listener on the dialog never heard it.
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        closeSettings();
    });
    settingsDialog.modal = modal;
}

/** Open the dialog on the page's library: its settings read now, and the rules to check them by. */
export function openSettings() {
    if (!settingsDialog.modal) buildSettingsDialog();
    settingsDialog.opener = document.activeElement;
    settingsDialog.modal.classList.remove('hidden');
    replaceContent(settingsDialog.body, buildElement('p', { className: 'settings-loading', text: 'Loading the settings...' }));
    settingsDialog.save.disabled = true;
    return Promise.all([api.json('/api/settings'), loadRules()])
        .then(([data]) => {
            if (!data || !Array.isArray(data.groups)) {
                replaceContent(settingsDialog.body, buildElement('p', { className: 'validation-error',
                    text: (data && data.error) || 'Could not read the settings.' }));
                return;
            }
            renderSettings(data);
        })
        .catch(err => {
            console.error('Could not read the settings:', err);
            replaceContent(settingsDialog.body, buildElement('p', { className: 'validation-error',
                text: 'Could not read the settings.' }));
        });
}

export function closeSettings() {
    if (!settingsDialog.modal) return;
    settingsDialog.modal.classList.add('hidden');
    if (settingsDialog.opener && typeof settingsDialog.opener.focus === 'function') settingsDialog.opener.focus();
}

/**
 * The locked groups whose every consequence the owner ticked: what the save names as
 * acknowledged. The server refuses a change to a locked setting without it
 * (tagpup.services.settings.change), so the lock holds for every caller, not only here.
 */
function acknowledgedGroups() {
    return [...settingsDialog.opened]
        .filter(([, boxes]) => boxes.every(box => box.checked))
        .map(([name]) => name);
}

/** Save what changed, as one change of the library's journal; reload after a locked one. */
export function saveSettings() {
    const values = changedValues();
    if (whyNotSave() !== null || !Object.keys(values).length) return Promise.resolve(null);
    settingsDialog.saving = true;
    refreshSave();
    say('Saving...');
    return api.json('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values, acknowledged: acknowledgedGroups() }),
    }).then(result => {
        settingsDialog.saving = false;
        if (!result || !result.success) {
            say((result && result.error) || 'The settings were not saved.');
            refreshSave();
            return result;
        }
        if (result.locked) {
            say('Saved. Reloading...');
            window.location.reload();
            return result;
        }
        say(result.changed ? `Saved ${result.changed} setting(s).` : 'Nothing needed saving.');
        return api.json('/api/settings').then(data => {
            if (data && Array.isArray(data.groups)) renderSettings(data);
            say(result.changed ? `Saved ${result.changed} setting(s).` : 'Nothing needed saving.');
            return result;
        });
    }).catch(err => {
        settingsDialog.saving = false;
        console.error('Could not save the settings:', err);
        say('The settings were not saved.');
        refreshSave();
        return null;
    });
}
