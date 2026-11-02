/**
 * app/static/app.js — минимальный фронт для Drug Deficit Predictor.
 *
 * Логика:
 * 1. Пользователь вводит запрос → fetch /api/search
 * 2. Рендерим карточки (одна на smnn_code).
 * 3. Клик на карточку → fetch /api/risk/{smnn_code} → модалка.
 *
 * Безопасность: используем textContent для пользовательских строк,
 * чтобы не было XSS через innerHTML.
 */

const API_BASE = '';

// ---------- Утилиты ----------

function $(id) { return document.getElementById(id); }

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function riskClassToCss(cls) {
    if (cls === 0) return 'low';
    if (cls === 1) return 'medium';
    if (cls === 2) return 'high';
    return 'none';
}

function riskLabel(cls, fallback) {
    if (cls === 0) return 'Низкий';
    if (cls === 1) return 'Средний';
    if (cls === 2) return 'Высокий';
    return fallback || 'Нет данных';
}

// ---------- Поиск ----------

async function doSearch(query) {
    const statusEl = $('status');
    const btn = $('search-btn');
    const resultsSection = $('results-section');
    const resultsEl = $('results');
    const titleEl = $('results-title');
    const metaEl = $('results-meta');

    statusEl.className = 'status';
    statusEl.textContent = 'Поиск...';
    btn.disabled = true;
    resultsSection.classList.add('hidden');

    try {
        const url = `${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=20`;
        const resp = await fetch(url);

        if (resp.status === 422) {
            const data = await resp.json();
            const msg = (data.detail && data.detail[0] && data.detail[0].msg) || 'Невалидный запрос';
            statusEl.className = 'status error';
            statusEl.textContent = 'Ошибка: ' + msg;
            return;
        }
        if (!resp.ok) {
            statusEl.className = 'status error';
            statusEl.textContent = 'Ошибка сервера (' + resp.status + ')';
            return;
        }

        const data = await resp.json();

        if (data.total === 0) {
            statusEl.className = 'status';
            statusEl.textContent = 'Ничего не найдено. Попробуйте другое название.';
            return;
        }

        statusEl.textContent = '';
        titleEl.textContent = `Найдено форм: ${data.total}`;
        metaEl.textContent = `Показано ${data.items.length} из ${data.total}`;

        renderResults(data.items);
        resultsSection.classList.remove('hidden');
    } catch (err) {
        console.error(err);
        statusEl.className = 'status error';
        statusEl.textContent = 'Не удалось выполнить запрос: ' + err.message;
    } finally {
        btn.disabled = false;
    }
}

function renderResults(items) {
    const container = $('results');
    container.innerHTML = '';

    for (const item of items) {
        const card = document.createElement('div');
        card.className = 'card';
        card.dataset.code = item.smnn_code;

        const trades = (item.trade_names || []).slice(0, 3).join(', ') +
            (item.trade_names && item.trade_names.length > 3
                ? ` и ещё ${item.trade_names.length - 3}`
                : '');

        const riskBadge = item.risk_preview
            ? `<span class="badge badge-${riskClassToCss(item.risk_preview.class)}">
                   ${escapeHtml(item.risk_preview.label)}
               </span>`
            : `<span class="badge badge-none">Нет данных</span>`;

        card.innerHTML = `
            <div class="card-inn">${escapeHtml(item.standard_inn)}</div>
            <div class="card-form">${escapeHtml(item.standard_form || '')}</div>
            <div class="card-dozes">${escapeHtml(item.standard_doze || '')}</div>
            <div class="card-trades" title="${escapeHtml((item.trade_names || []).join(', '))}">
                <strong>ТН:</strong> ${escapeHtml(trades)}
            </div>
            ${riskBadge}
        `;

        card.addEventListener('click', () => openRisk(item.smnn_code));
        container.appendChild(card);
    }
}

// ---------- Модалка прогноза ----------

async function openRisk(smnn_code) {
    const modal = $('modal');
    const body = $('modal-body');
    body.innerHTML = '<p>Загрузка прогноза...</p>';
    modal.classList.remove('hidden');

    try {
        const url = `${API_BASE}/api/risk/${encodeURIComponent(smnn_code)}`;
        const resp = await fetch(url);

        if (resp.status === 404) {
            body.innerHTML = '<p>СМНН не найден.</p>';
            return;
        }
        if (!resp.ok) {
            body.innerHTML = `<p>Ошибка сервера (${resp.status})</p>`;
            return;
        }

        const data = await resp.json();
        renderRiskModal(data);
    } catch (err) {
        console.error(err);
        body.innerHTML = '<p>Не удалось загрузить прогноз.</p>';
    }
}

function renderRiskModal(data) {
    const body = $('modal-body');

    const subtitle = [data.standard_form, data.standard_doze]
        .filter(Boolean)
        .join(', ') || '—';

    const tradesHtml = (data.trade_names && data.trade_names.length)
        ? `<p class="modal-subtitle">ТН: ${escapeHtml(data.trade_names.join(', '))}</p>`
        : '';

    // Блок риска
    let riskHtml = '';
    if (data.risk) {
        const cls = riskClassToCss(data.risk.class);
        const p = (data.risk.probability * 100).toFixed(1);
        const pl = (data.risk.probabilities.low * 100).toFixed(1);
        const pm = (data.risk.probabilities.medium * 100).toFixed(1);
        const ph = (data.risk.probabilities.high * 100).toFixed(1);

        riskHtml = `
            <div class="risk-block risk-${cls}">
                <div>
                    <div class="risk-label" style="color: var(--color-${cls});">
                        ${escapeHtml(data.risk.label)} риск
                    </div>
                    <div class="risk-prob">Вероятность: ${p}%</div>
                </div>
            </div>
            <div class="proba-bar">
                <div class="proba-seg proba-low" style="width: ${pl}%">${pl}%</div>
                <div class="proba-seg proba-medium" style="width: ${pm}%">${pm}%</div>
                <div class="proba-seg proba-high" style="width: ${ph}%">${ph}%</div>
            </div>
        `;
    } else {
        riskHtml = `
            <div class="risk-block risk-none">
                <div>
                    <div class="risk-label" style="color: var(--color-muted);">Нет данных</div>
                    <div class="risk-prob">Недостаточно данных для прогноза</div>
                </div>
            </div>
        `;
    }

    // Snapshot
    const snap = data.features_snapshot || {};
    const featuresHtml = `
        <h3 class="section-title">Ключевые признаки</h3>
        <dl class="features-list">
            <div><dt>Препаратов-позиций</dt><dd>${snap.products_count ?? '—'}</dd></div>
            <div><dt>Производителей</dt><dd>${snap.producers_count ?? '—'}</dd></div>
            <div><dt>Средний возраст (лет)</dt><dd>${(snap.avg_age ?? 0).toFixed(1)}</dd></div>
            <div><dt>ЖНВЛП</dt><dd>${snap.is_essential ? 'да' : 'нет'}</dd></div>
            <div><dt>Наркотический</dt><dd>${snap.is_narcotic ? 'да' : 'нет'}</dd></div>
            <div><dt>Аналоги по ATC</dt><dd>${snap.has_atc_substitutes ? 'есть' : 'нет'}</dd></div>
        </dl>
    `;

    // Рекомендации
    const recs = data.recommendations || [];
    const recsHtml = recs.length
        ? `
        <h3 class="section-title">Рекомендации</h3>
        <ul class="recs-list">
            ${recs.map(r => {
                const critical = r.startsWith('⚠️') || /эскалировать|немедленно|лицензию/i.test(r);
                return `<li class="${critical ? 'critical' : ''}">${escapeHtml(r)}</li>`;
            }).join('')}
        </ul>`
        : '';

    body.innerHTML = `
        <h2>${escapeHtml(data.standard_inn)}</h2>
        <p class="modal-subtitle">${escapeHtml(subtitle)}</p>
        ${tradesHtml}
        ${riskHtml}
        ${featuresHtml}
        ${recsHtml}
    `;
}

// ---------- Инициализация ----------

document.addEventListener('DOMContentLoaded', () => {
    const form = $('search-form');
    const input = $('search-input');
    const modal = $('modal');
    const modalClose = $('modal-close');

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (q) doSearch(q);
    });

    modalClose.addEventListener('click', () => modal.classList.add('hidden'));
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.add('hidden');
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') modal.classList.add('hidden');
    });

    // Автопоиск из URL ?q=...
    const params = new URLSearchParams(window.location.search);
    const initialQ = params.get('q');
    if (initialQ) {
        input.value = initialQ;
        doSearch(initialQ);
    }
});