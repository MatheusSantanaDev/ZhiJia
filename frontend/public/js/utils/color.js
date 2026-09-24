export function rgbToHsl(r, g, b) {
    r /= 255;
    g /= 255;
    b /= 255;

    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    let h = (max + min) / 2;
    let s = h;
    let l = h;

    if (max === min) {
        h = s = 0;
    } else {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === r) {
            h = (g - b) / d + (g < b ? 6 : 0);
        } else if (max === g) {
            h = (b - r) / d + 2;
        } else {
            h = (r - g) / d + 4;
        }
        h /= 6;
    }
    return [h, s, l];
}

export function hslToRgb(h, s, l) {
    let r, g, b;

    if (s === 0) {
        r = g = b = l;
    } else {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };

        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }

    return [r * 255, g * 255, b * 255];
}

export function getComplementaryColor(r, g, b) {
    if (r === 0 && g === 0 && b === 0) return 'rgb(255, 255, 255)';
    if (r === 255 && g === 255 && b === 255) return 'rgb(0, 0, 0)';

    const hsl = rgbToHsl(r, g, b);
    const compH = (hsl[0] + 0.5) % 1;
    const [compR, compG, compB] = hslToRgb(compH, hsl[1], hsl[2]);
    return `rgb(${Math.round(compR)}, ${Math.round(compG)}, ${Math.round(compB)})`;
}

export function applyColor(r, g, b) {
    const bgColor = `rgb(${r}, ${g}, ${b})`;
    document.body.style.backgroundColor = bgColor;
    document.getElementById('title').style.color = getComplementaryColor(r, g, b);
}