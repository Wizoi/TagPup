// TagPup's page: the tag tree, and its Manage Tags dialog.
import { api } from './common/api.js';
import { nameProblem, tagProblem } from './common/vocabulary.js';
import { state } from './state.js';
import {
    btnCloseTaxonomy, btnCloseTaxonomyFooter, btnManageTaxonomy, btnTaxonomyAddRoot, statusDot,
    statusText, taxonomyModal, taxonomySearchInput
} from './elements.js';
import { fetchKnownTagsAndPeople, loadTaxonomy } from './tags.js';
import { scanFolder } from './folder.js';

export function wireTaxonomyModal() {
    if (btnManageTaxonomy && taxonomyModal) {
        btnManageTaxonomy.addEventListener('click', () => {
            taxonomyModal.classList.add('active');
            renderTaxonomyTree();
        });
        
        const closeTaxonomy = () => {
            taxonomyModal.classList.remove('active');
        };
        
        btnCloseTaxonomy.addEventListener('click', closeTaxonomy);
        btnCloseTaxonomyFooter.addEventListener('click', closeTaxonomy);
        
        btnTaxonomyAddRoot.addEventListener('click', () => {
            const name = prompt("Enter name of new root category:");
            if (name && name.trim()) {
                const hasFace = confirm(`Enable Face Matching for "${name}" (e.g. for people or pets)?`);
                createTaxonomyNode(name.trim(), null, hasFace ? 1 : 0);
            }
        });
        
        taxonomySearchInput.addEventListener('input', () => {
            renderTaxonomyTree();
        });
    }
}

export function renderTaxonomyTree() {
    const container = document.getElementById('taxonomy-tree-container');
    if (!container) return;
    const searchVal = taxonomySearchInput.value.toLowerCase().trim();
    container.innerHTML = '';
    
    const nodesById = {};
    state.taxonomyNodes.forEach(node => {
        nodesById[node.id] = { ...node, children: [] };
    });
    
    const roots = [];
    Object.values(nodesById).forEach(node => {
        if (node.parent_id === null) {
            roots.push(node);
        } else {
            const parent = nodesById[node.parent_id];
            if (parent) {
                parent.children.push(node);
            } else {
                roots.push(node);
            }
        }
    });
    
    function matchesSearch(node) {
        if (!searchVal) return true;
        if (node.tag.toLowerCase().includes(searchVal)) return true;
        return node.children.some(child => matchesSearch(child));
    }
    
    const filteredRoots = roots.filter(matchesSearch);
    
    if (filteredRoots.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">No tags match your search or taxonomy is empty.</div>';
        return;
    }
    
    const ul = document.createElement('ul');
    ul.className = 'taxonomy-tree-list';
    
    filteredRoots.forEach(root => {
        ul.appendChild(createNodeElement(root, nodesById));
    });
    
    container.appendChild(ul);
}

export function createNodeElement(node, nodesById) {
    const li = document.createElement('li');
    li.className = 'taxonomy-node';
    li.setAttribute('data-id', node.id);
    
    const content = document.createElement('div');
    content.className = 'taxonomy-node-content';
    
    const expander = document.createElement('span');
    expander.className = 'taxonomy-node-expander';
    if (node.children && node.children.length > 0) {
        expander.textContent = '▶';
        expander.onclick = (e) => {
            e.stopPropagation();
            const sublist = li.querySelector('.taxonomy-sublist');
            if (sublist) {
                if (sublist.classList.contains('hidden')) {
                    sublist.classList.remove('hidden');
                    expander.textContent = '▼';
                } else {
                    sublist.classList.add('hidden');
                    expander.textContent = '▶';
                }
            }
        };
    } else {
        expander.textContent = '•';
        expander.style.cursor = 'default';
    }
    content.appendChild(expander);
    
    const nameSpan = document.createElement('span');
    nameSpan.className = 'taxonomy-node-name';
    nameSpan.textContent = node.name;
    content.appendChild(nameSpan);
    
    const metaSpan = document.createElement('span');
    metaSpan.className = 'taxonomy-node-meta';
    metaSpan.textContent = `${node.usage_count} ${node.usage_count === 1 ? 'img' : 'imgs'}`;
    content.appendChild(metaSpan);
    
    const actions = document.createElement('div');
    actions.className = 'taxonomy-node-actions';
    
    if (node.parent_id === null) {
        const faceMatchLabel = document.createElement('label');
        faceMatchLabel.style.display = 'inline-flex';
        faceMatchLabel.style.alignItems = 'center';
        faceMatchLabel.style.gap = '6px';
        faceMatchLabel.style.fontSize = '12px';
        faceMatchLabel.style.marginRight = '12px';
        faceMatchLabel.title = "Enable face matching for this category (People/Pets)";
        
        const labelText = document.createElement('span');
        labelText.textContent = 'Face Matching:';
        faceMatchLabel.appendChild(labelText);
        
        const switchLabel = document.createElement('label');
        switchLabel.className = 'switch';
        
        const faceCheckbox = document.createElement('input');
        faceCheckbox.type = 'checkbox';
        faceCheckbox.checked = node.has_face === 1;
        faceCheckbox.onchange = (e) => {
            updateTaxonomyNode(node.id, { has_face: e.target.checked ? 1 : 0 });
        };
        switchLabel.appendChild(faceCheckbox);
        
        const sliderSpan = document.createElement('span');
        sliderSpan.className = 'slider';
        switchLabel.appendChild(sliderSpan);
        
        faceMatchLabel.appendChild(switchLabel);
        actions.appendChild(faceMatchLabel);
    } else {
        if (node.has_face === 1) {
            const badge = document.createElement('span');
            badge.className = 'taxonomy-node-badge badge-people';
            badge.textContent = 'Face Match';
            actions.appendChild(badge);
        }
    }
    
    const hideLabel = document.createElement('label');
    hideLabel.style.display = 'inline-flex';
    hideLabel.style.alignItems = 'center';
    hideLabel.style.gap = '4px';
    hideLabel.style.fontSize = '12px';
    hideLabel.style.marginRight = '8px';
    hideLabel.title = "Hide this tag and its sub-tags from autocomplete popups for new images";
    
    const hideCheckbox = document.createElement('input');
    hideCheckbox.type = 'checkbox';
    hideCheckbox.checked = node.hidden_from_autocomplete === 1;
    hideCheckbox.onchange = (e) => {
        updateTaxonomyNode(node.id, { hidden_from_autocomplete: e.target.checked ? 1 : 0 });
    };
    hideLabel.appendChild(hideCheckbox);
    
    const hideText = document.createElement('span');
    hideText.textContent = 'Hide';
    hideLabel.appendChild(hideText);
    actions.appendChild(hideLabel);
    
    const btnAdd = document.createElement('button');
    btnAdd.className = 'btn btn-secondary btn-sm';
    btnAdd.style.padding = '2px 6px';
    btnAdd.style.fontSize = '11px';
    btnAdd.textContent = '➕ Add';
    btnAdd.onclick = (e) => {
        e.stopPropagation();
        const childName = prompt(`Enter name of new subtag under "${node.tag}":`);
        if (childName && childName.trim()) {
            createTaxonomyNode(childName.trim(), node.id);
        }
    };
    actions.appendChild(btnAdd);

    const btnRename = document.createElement('button');
    btnRename.className = 'btn btn-secondary btn-sm';
    btnRename.style.padding = '2px 6px';
    btnRename.style.fontSize = '11px';
    btnRename.textContent = '✏️ Rename';
    btnRename.onclick = (e) => {
        e.stopPropagation();
        const newName = prompt(`Enter new name for tag "${node.name}":`, node.name);
        if (newName && newName.trim() && newName.trim() !== node.name) {
            renameTaxonomyNode(node.id, newName.trim());
        }
    };
    actions.appendChild(btnRename);
    
    const btnDel = document.createElement('button');
    btnDel.className = 'btn btn-secondary btn-sm';
    btnDel.style.padding = '2px 6px';
    btnDel.style.fontSize = '11px';
    btnDel.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
    btnDel.style.color = '#f87171';
    btnDel.style.borderColor = 'rgba(239, 68, 68, 0.2)';
    btnDel.textContent = '🗑️';
    btnDel.onclick = (e) => {
        e.stopPropagation();
        deleteTaxonomyNode(node.id, node.tag);
    };
    actions.appendChild(btnDel);
    
    content.appendChild(actions);
    li.appendChild(content);
    
    if (node.children && node.children.length > 0) {
        const sublist = document.createElement('ul');
        sublist.className = 'taxonomy-sublist hidden';
        
        node.children.forEach(child => {
            sublist.appendChild(createNodeElement(child, nodesById));
        });
        
        li.appendChild(sublist);
    }
    
    return li;
}

export function updateTaxonomyNode(id, fields) {
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Updating taxonomy...';
    
    api.json('/api/taxonomy/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, ...fields })
    })
    .then(data => {
        if (data.success) {
            loadTaxonomy().then(() => {
                renderTaxonomyTree();
                fetchKnownTagsAndPeople();
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            });
        } else {
            alert("Error updating tag: " + data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
    });
}

export function createTaxonomyNode(name, parentId = null, hasFace = 0) {
    const problem = tagProblem(name);
    if (problem) {
        alert(problem);
        return;
    }
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Creating tag...';
    
    api.json('/api/taxonomy/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, parent_id: parentId, has_face: hasFace })
    })
    .then(data => {
        if (data.success) {
            loadTaxonomy().then(() => {
                renderTaxonomyTree();
                fetchKnownTagsAndPeople();
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            });
        } else {
            alert("Error creating tag: " + data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
    });
}

export function deleteTaxonomyNode(id, tagPath) {
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Checking usage...';
    
    api.json('/api/taxonomy/delete-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: id })
    })
    .then(async (data) => {
        if (!data.success) {
            alert("Error checking tag usage: " + data.error);
            return;
        }
        
        let confirmResult = { action: 'remove' };
        
        if (data.used) {
            const possibleTargets = state.taxonomyNodes
                .filter(n => n.id !== id && !n.tag.startsWith(tagPath + "/"))
                .map(n => n.tag);
                
            confirmResult = await showDeleteConflictModal(tagPath, data.count, possibleTargets);
            if (!confirmResult) {
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                return;
            }
        } else {
            const confirmed = confirm(`Are you sure you want to remove tag "${tagPath}"?`);
            if (!confirmed) {
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                return;
            }
        }
        
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Deleting tag...';
        
        api.json('/api/taxonomy/delete-confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tag_id: id,
                action: confirmResult.action,
                target_tag: confirmResult.target_tag
            })
        })
        .then(resData => {
            if (resData.success) {
                loadTaxonomy().then(() => {
                    renderTaxonomyTree();
                    fetchKnownTagsAndPeople();
                    if (data.used && state.scannedFolder) {
                        scanFolder(true);
                    }
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                });
            } else {
                alert("Error deleting tag: " + resData.error);
                // A partial delete rewrote some photos; show them as they are now.
                if (resData.photos_rewritten && state.scannedFolder) scanFolder(true);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            }
        });
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
    });
}

export function renameTaxonomyNode(tagId, newName) {
    // A node's own name is one level: no "/" either.
    const problem = nameProblem(newName);
    if (problem) {
        alert(problem);
        return;
    }
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Renaming tag...';
    
    api.json('/api/taxonomy/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId, new_name: newName })
    })
    .then(data => {
        if (data.success) {
            // Some photos may not have been rewritten; they still carry the old tag.
            if (data.warning) alert("Renamed, but " + data.warning);
            loadTaxonomy().then(() => {
                renderTaxonomyTree();
                fetchKnownTagsAndPeople();
                if (state.scannedFolder) {
                    scanFolder(true);
                }
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            });
        } else {
            alert("Error renaming tag: " + data.error);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
    });
}

export function showDeleteConflictModal(tagName, count, targetTagsOptions) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        
        const dropdownHtml = targetTagsOptions.map(t => `<option value="${t}">${t}</option>`).join('');
        
        overlay.innerHTML = `
                <div class="modal-container" style="max-width: 500px;">
                    <div class="modal-header">
                        <h2>Tag Removal Check</h2>
                        <button class="modal-close-btn">&times;</button>
                    </div>
                    <div class="modal-body">
                        <p style="margin-bottom: 16px; line-height: 1.5; font-size: 14px;">
                            The tag <strong style="color: var(--accent);">${tagName}</strong> is used by <strong>${count}</strong> photos. 
                            Removing it requires clean up. Please choose how you want to handle these photos:
                        </p>
                        <div class="placement-options">
                            <label class="placement-option-label">
                                <input type="radio" name="delete-opt" value="remove" checked>
                                <span>Remove this tag from all affected photos</span>
                            </label>
                            <label class="placement-option-label">
                                <input type="radio" name="delete-opt" value="move">
                                <span>Move affected photos to another tag</span>
                            </label>
                        </div>
                        <div id="move-tag-dropdown-container" style="display: none; padding-left: 24px; margin-top: 8px;">
                            <select id="move-target-select" class="taxonomy-search-input" style="width: 100%;">
                                <option value="">-- Select Target Tag --</option>
                                ${dropdownHtml}
                            </select>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary btn-cancel">Cancel</button>
                        <button class="btn btn-primary btn-confirm">Confirm</button>
                    </div>
                </div>
            `;
        
        document.body.appendChild(overlay);
        
        const radioMove = overlay.querySelector('input[value="move"]');
        const dropdownContainer = overlay.querySelector('#move-tag-dropdown-container');
        
        overlay.querySelectorAll('input[name="delete-opt"]').forEach(rad => {
            rad.addEventListener('change', (e) => {
                dropdownContainer.style.display = e.target.value === 'move' ? 'block' : 'none';
            });
        });
        
        const close = (value) => {
            overlay.className = 'modal-overlay';
            setTimeout(() => overlay.remove(), 300);
            resolve(value);
        };
        
        overlay.querySelector('.modal-close-btn').onclick = () => close(null);
        overlay.querySelector('.btn-cancel').onclick = () => close(null);
        
        overlay.querySelector('.btn-confirm').onclick = () => {
            const selected = overlay.querySelector('input[name="delete-opt"]:checked').value;
            if (selected === 'move') {
                const target = overlay.querySelector('#move-target-select').value;
                if (!target) {
                    alert("Please select a target tag.");
                    return;
                }
                close({ action: 'move', target_tag: target });
            } else {
                close({ action: 'remove' });
            }
        };
    });
}
