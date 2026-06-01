/**
 * mascot.js — Mascot Animation Engine
 * 
 * Handles all logo/bot mascot interactions:
 * - Cursor tracking (Teacher-Student parallax)
 * - Jiggle/Pop/Hit animations
 * - Bot visual state updates
 * - Mascot drag-drop zone
 */

import { state } from './state.js';

const CONSTANTS = {
    MAX_ROTATION: 35,
    SETTLE_DELAY: 2000
};

/**
 * Extreme Watchful Teacher Tracking — parallax logo follows cursor.
 */
function trackCursor(e) {
    const logo = document.getElementById('main-logo-img');
    if (!logo || window.innerWidth <= 850) return;
    if (state.tiltSettleTimer) clearTimeout(state.tiltSettleTimer);
    const rect = logo.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const dx = e.clientX - centerX;
    const dy = e.clientY - centerY;
    const rotX = Math.max(-CONSTANTS.MAX_ROTATION, Math.min(CONSTANTS.MAX_ROTATION, -dy / 12));
    const rotY = Math.max(-CONSTANTS.MAX_ROTATION, Math.min(CONSTANTS.MAX_ROTATION, dx / 12));
    const moveX = Math.max(-10, Math.min(10, dx / 50));
    const moveY = Math.max(-10, Math.min(10, dy / 50));
    logo.style.transform = `perspective(600px) rotateX(${rotX}deg) rotateY(${rotY}deg) translate3d(${moveX}px, ${moveY}px, 0)`;
    state.tiltSettleTimer = setTimeout(() => {
        logo.style.transform = `perspective(600px) rotateX(0) rotateY(0) translate3d(0, 0, 0)`;
    }, CONSTANTS.SETTLE_DELAY);
}

function resetTilt() {
    const logo = document.getElementById('main-logo-img');
    if (logo) logo.style.transform = 'perspective(600px) rotateX(0) rotateY(0) translate3d(0, 0, 0)';
}

function popBot() {
    const logo = document.getElementById('main-logo-img');
    if (logo) {
        logo.classList.add('logo-pop');
        setTimeout(() => logo.classList.remove('logo-pop'), 600);
    }
}

function hitBot() {
    const logo = document.getElementById('main-logo-img');
    if (logo) {
        logo.classList.add('logo-jiggle');
        setTimeout(() => logo.classList.remove('logo-jiggle'), 500);
    }
}

function jiggleLogo() { hitBot(); }

function triggerBotReaction(txt) {
    const low = txt.toLowerCase();
    if (low.match(/\b(hi|hello|hey)\b/)) {
        state.set('botState', 'wave');
        window.botState = 'wave';
        setTimeout(() => { state.set('botState', 'idle'); window.botState = 'idle'; updateBotVisuals(); }, 3000);
    } else if (low.includes("how are you")) {
        state.set('botState', 'thumbsUp');
        window.botState = 'thumbsUp';
        setTimeout(() => { state.set('botState', 'idle'); window.botState = 'idle'; updateBotVisuals(); }, 3000);
    }
    updateBotVisuals();
}

function updateBotVisuals() {
    document.querySelectorAll('.bot-bubble').forEach(b => {
        b.style.display = state.botState !== 'idle' ? 'block' : 'none';
    });
}

/**
 * Initialize mascot drag-drop zone for Neural Context retrieval.
 */
function initMascotDrop(retrieveContextFn) {
    const m = document.getElementById('mascot-container');
    if (!m) return;
    m.ondragover = (e) => { e.preventDefault(); m.classList.add('mascot-drop-active'); };
    m.ondragleave = () => m.classList.remove('mascot-drop-active');
    m.ondrop = async (e) => {
        e.preventDefault();
        m.classList.remove('mascot-drop-active');
        const txt = e.dataTransfer.getData('text/plain');
        if (txt) retrieveContextFn(txt);
    };
}

/**
 * Bind mouse listeners for cursor tracking.
 */
function bindMouseListeners() {
    document.addEventListener('mousemove', trackCursor);
    document.addEventListener('mouseleave', resetTilt);
    
    const logoImg = document.getElementById('main-logo-img');
    if (logoImg) logoImg.addEventListener('click', jiggleLogo);
}

const mascot = {
    trackCursor, resetTilt, popBot, hitBot, jiggleLogo,
    triggerBotReaction, updateBotVisuals,
    initMascotDrop, bindMouseListeners
};

export { mascot };
