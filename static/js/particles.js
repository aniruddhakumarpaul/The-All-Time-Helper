/**
 * particles.js
 * Custom interactive particle system for The All Time Helper - Pro - 3D/HD Upgrade
 */
const canvas = document.getElementById('particle-canvas');
const ctx = canvas.getContext('2d');

let particles = [];
const particleCount = 35; // Slightly fewer for better HD performance
let mouse = { x: null, y: null };

// Mobile Glide Support
function handlePointer(x, y) {
    mouse.x = x;
    mouse.y = y;
}

window.addEventListener('mousemove', (e) => handlePointer(e.clientX, e.clientY));
window.addEventListener('touchstart', (e) => handlePointer(e.touches[0].clientX, e.touches[0].clientY));
window.addEventListener('touchmove', (e) => {
    handlePointer(e.touches[0].clientX, e.touches[0].clientY);
}, { passive: true });
window.addEventListener('touchend', () => { mouse.x = null; mouse.y = null; });

window.addEventListener('resize', () => initCanvas());

function initCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}

class Particle {
    constructor() { this.init(); }
    init() {
        this.x = Math.random() * canvas.width;
        this.y = Math.random() * canvas.height;
        this.size = Math.random() * 4 + 1; // Smaller, more refined pebbles
        this.speedX = Math.random() * 0.8 - 0.4;
        this.speedY = Math.random() * 0.8 - 0.4;
        
        const isDark = document.body.getAttribute('data-theme') === 'dark';
        this.baseColor = isDark ? '66, 133, 244' : '30, 100, 250';
    }
    update() {
        this.x += this.speedX;
        this.y += this.speedY;

        if (this.x > canvas.width + 50) this.x = -50;
        else if (this.x < -50) this.x = canvas.width + 50;
        if (this.y > canvas.height + 50) this.y = -50;
        else if (this.y < -50) this.y = canvas.height + 50;

        // Interaction physics
        if (mouse.x !== null) {
            let dx = mouse.x - this.x;
            let dy = mouse.y - this.y;
            let distance = Math.sqrt(dx * dx + dy * dy);
            if (distance < 120) {
                const force = (120 - distance) / 120;
                this.x -= dx * force * 0.1;
                this.y -= dy * force * 0.1;
            }
        }
    }
    draw() {
        // Flat Minimalist Style
        ctx.fillStyle = `rgba(${this.baseColor}, 0.25)`;
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fill();
    }
}

function createParticles() {
    particles = [];
    for (let i = 0; i < particleCount; i++) particles.push(new Particle());
}

function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < particles.length; i++) {
        particles[i].update();
        particles[i].draw();
    }
    requestAnimationFrame(animate);
}

initCanvas();
createParticles();
animate();

const observer = new MutationObserver(() => {
    particles.forEach(p => p.init());
});
observer.observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
