/**
 * chat_engine.js - Motor compartido de chat, renderizado y utilidades para ACCN
 */

(function (window) {
    'use strict';

    function escapeHtml(texto) {
        return String(texto ?? '').replace(/[&<>"']/g, function (caracter) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[caracter];
        });
    }

    function renderMarkdown(texto) {
        const lineas = String(texto ?? '').replace(/\r/g, '').split('\n');
        const salida = [];
        const celdas = linea => linea.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(celda => celda.trim());
        const esSeparadorTabla = linea => celdas(linea).length > 1 && celdas(linea).every(celda => /^:?-{3,}:?$/.test(celda));
        const inline = valor => escapeHtml(valor)
            .replace(/`([^`]+)`/g, '<code class="rounded bg-slate-100 px-1 py-0.5 font-mono text-blue-800">$1</code>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/__(.+?)__/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');

        for (let i = 0; i < lineas.length;) {
            if (!lineas[i].trim()) { i++; continue; }
            if (i + 1 < lineas.length && lineas[i].includes('|') && esSeparadorTabla(lineas[i + 1])) {
                const encabezados = celdas(lineas[i]);
                i += 2;
                const filas = [];
                while (i < lineas.length && lineas[i].includes('|') && lineas[i].trim()) filas.push(celdas(lineas[i++]));
                salida.push(`<div class="my-3 max-w-full overflow-x-auto rounded-lg border border-slate-200"><table class="w-full border-collapse text-left text-xs"><thead class="bg-slate-100 text-slate-700"><tr>${encabezados.map(celda => `<th class="border-b border-slate-200 px-3 py-2 font-semibold">${inline(celda)}</th>`).join('')}</tr></thead><tbody>${filas.map(fila => `<tr class="odd:bg-white even:bg-slate-50">${encabezados.map((_, indice) => `<td class="border-t border-slate-100 px-3 py-2 align-top">${inline(fila[indice] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
                continue;
            }
            const heading = lineas[i].match(/^(#{1,3})\s+(.+)$/);
            if (heading) {
                const nivel = heading[1].length;
                salida.push(`<h${nivel + 2} class="mb-1 mt-3 font-bold text-slate-900">${inline(heading[2])}</h${nivel + 2}>`);
                i++;
                continue;
            }
            if (/^\s*(?:[-*]|\d+\.)\s+/.test(lineas[i])) {
                const numerada = /^\s*\d+\./.test(lineas[i]);
                const items = [];
                while (i < lineas.length && /^\s*(?:[-*]|\d+\.)\s+/.test(lineas[i])) {
                    items.push(lineas[i++].replace(/^\s*(?:[-*]|\d+\.)\s+/, ''));
                }
                const tag = numerada ? 'ol' : 'ul';
                salida.push(`<${tag} class="my-2 list-inside ${numerada ? 'list-decimal' : 'list-disc'} space-y-1 pl-2">${items.map(item => `<li>${inline(item)}</li>`).join('')}</${tag}>`);
                continue;
            }
            const parrafo = [];
            while (i < lineas.length && lineas[i].trim() && !(i + 1 < lineas.length && lineas[i].includes('|') && esSeparadorTabla(lineas[i + 1])) && !/^(#{1,3})\s+/.test(lineas[i]) && !/^\s*(?:[-*]|\d+\.)\s+/.test(lineas[i])) {
                parrafo.push(lineas[i++]);
            }
            salida.push(`<p class="my-2 whitespace-pre-wrap">${inline(parrafo.join('\n')).replace(/\n/g, '<br>')}</p>`);
        }
        return salida.join('');
    }

    async function copiarAlPortapapeles(boton, texto) {
        try {
            await navigator.clipboard.writeText(texto);
            const originalHtml = boton.innerHTML;
            boton.innerHTML = `
                <svg class="w-3.5 h-3.5 text-emerald-600 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                </svg>
                <span class="text-emerald-600 font-semibold">Copiado</span>
            `;
            setTimeout(() => {
                boton.innerHTML = originalHtml;
            }, 2000);
        } catch (_) {
            if (window.showAppToast) {
                window.showAppToast('No se pudo copiar el texto.', 'error');
            }
        }
    }

    window.ChatEngine = {
        escapeHtml: escapeHtml,
        renderMarkdown: renderMarkdown,
        copiarAlPortapapeles: copiarAlPortapapeles
    };

})(window);
