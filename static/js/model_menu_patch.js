// model_menu_patch.js
// Keeps the visible model menu and command palette aligned with backend cloud routing.
(function () {
    var cloudModels = [
        { id: 'agentic-pro', label: 'GLM 5.2 Agentic', name: 'GLM 5.2 Agentic (OpenRouter)' },
        { id: 'openrouter-auto', label: 'OpenRouter Auto', name: 'OpenRouter Auto' },
        { id: 'openrouter-claude-sonnet-5', label: 'Claude Sonnet 5', name: 'Claude Sonnet 5 (OpenRouter)' },
        { id: 'openrouter-kimi-code', label: 'Kimi K2.7 Code', name: 'Kimi K2.7 Code (OpenRouter)' },
        { id: 'openrouter-laguna-code', label: 'Laguna XS Code', name: 'Laguna XS Code (OpenRouter)' },
        { id: 'openrouter-nemotron-free', label: 'Nemotron 3 Ultra Free', name: 'Nemotron 3 Ultra Free (OpenRouter)' }
    ];

    var localModels = [
        { id: 'gemma4:e2b', label: 'Gemma 4', name: 'Gemma 4' },
        { id: 'gemma2:2b', label: 'Gemma 2', name: 'Gemma 2 (Fast&Fun)' },
        { id: 'dolphin-mistral', label: 'Dolphin Mistral', name: 'Mistral (Uncensored)' },
        { id: 'helper', label: 'Llama Sensitive', name: 'Llama (Sensitive)' },
        { id: 'phi3', label: 'Phi 3', name: 'Phi 3' },
        { id: 'moondream', label: 'Moondream Vision', name: 'Moondream (Vision)' }
    ];

    function selectModel(model) {
        if (typeof window.selModel === 'function') window.selModel(model.id, model.name);
        var menu = document.getElementById('model-menu');
        if (menu) menu.classList.remove('active');
    }

    function addHeader(menu, text) {
        var header = document.createElement('div');
        header.className = 'dropdown-header';
        header.textContent = text;
        menu.appendChild(header);
    }

    function addOption(menu, model) {
        var option = document.createElement('div');
        option.className = 'model-opt';
        option.dataset.modelId = model.id;
        option.dataset.modelName = model.name;
        option.textContent = model.label;
        option.addEventListener('click', function () { selectModel(model); });
        menu.appendChild(option);
    }

    function installModelMenu() {
        var menu = document.getElementById('model-menu');
        if (!menu || menu.dataset.cloudRoutingMenu === 'true') return;
        menu.dataset.cloudRoutingMenu = 'true';
        menu.textContent = '';
        addHeader(menu, 'Cloud via OpenRouter');
        cloudModels.forEach(function (model) { addOption(menu, model); });
        addHeader(menu, 'Local (Private)');
        localModels.forEach(function (model) { addOption(menu, model); });
    }

    function augmentPalette(query) {
        var list = document.getElementById('pal-results');
        if (!list) return;
        list.querySelectorAll('.cloud-model-pal-item').forEach(function (item) { item.remove(); });
        var q = String(query || '').trim().toLowerCase();
        cloudModels.filter(function (model) {
            return !q || ('model: ' + model.label).toLowerCase().includes(q) || model.id.toLowerCase().includes(q);
        }).slice(0, 6).reverse().forEach(function (model) {
            var row = document.createElement('div');
            row.className = 'pal-item cloud-model-pal-item';
            var icon = document.createElement('span');
            icon.className = 'pal-icon';
            icon.textContent = '🧠';
            var label = document.createElement('span');
            label.textContent = 'Model: ' + model.label;
            row.append(icon, label);
            row.addEventListener('click', function () {
                selectModel(model);
                if (typeof window.closePalette === 'function') window.closePalette();
            });
            list.prepend(row);
        });
    }

    function patchPalette() {
        if (window.__cloudRoutingPalettePatched) return;
        if (typeof window.updPal !== 'function') return;
        window.__cloudRoutingPalettePatched = true;
        var originalUpdPal = window.updPal;
        window.updPal = function (query) {
            originalUpdPal(query);
            augmentPalette(query);
        };
        var input = document.getElementById('pal-in');
        if (input) input.addEventListener('input', function () { setTimeout(function () { augmentPalette(input.value); }, 0); });
    }

    function init() {
        installModelMenu();
        patchPalette();
        setTimeout(function () { installModelMenu(); patchPalette(); }, 500);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
    window.installCloudRoutingModelMenu = installModelMenu;
})();
