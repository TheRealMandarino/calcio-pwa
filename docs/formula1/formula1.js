(function initializeFormula1View(root) {
    'use strict';

    const statusEl = root.document.getElementById('formula1Status');
    let requestInProgress = false;
    let cachedData = null;

    function showStatus(message, isError = false) {
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.classList.toggle('is-error', Boolean(isError));
        statusEl.classList.remove('hidden');
    }

    function formatDate(isoDate) {
        if (!isoDate) return '—';
        try {
            const date = new Date(isoDate);
            return date.toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' });
        } catch (_) {
            return isoDate;
        }
    }

    function renderNextRacePredictions(nextRace) {
        const titleEl = root.document.getElementById('f1NextRaceTitle');
        const circuitEl = root.document.getElementById('f1NextRaceCircuit');
        const locationEl = root.document.getElementById('f1NextRaceLocation');
        const dateEl = root.document.getElementById('f1NextRaceDate');
        const tbody = root.document.getElementById('f1PredictionsTbody');

        if (!nextRace) {
            if (titleEl) titleEl.textContent = 'Stagione conclusa';
            if (circuitEl) circuitEl.textContent = 'Nessun Gran Premio in calendario';
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--muted); padding: 18px;">Tutti i Gran Premi della stagione sono stati completati.</td></tr>';
            return;
        }

        if (titleEl) titleEl.textContent = `Round ${nextRace.round} · ${nextRace.race_name}`;
        if (circuitEl) circuitEl.textContent = `🏁 ${nextRace.circuit_name || 'Circuito non specificato'}`;
        if (locationEl) locationEl.textContent = `📍 ${[nextRace.locality, nextRace.country].filter(Boolean).join(', ')}`;
        if (dateEl) dateEl.textContent = `📅 ${formatDate(nextRace.date)}${nextRace.time ? ' ' + nextRace.time.slice(0, 5) + ' UTC' : ''}`;

        const summaryNextGp = root.document.getElementById('f1NextGpSummary');
        const summaryNextGpDate = root.document.getElementById('f1NextGpDateSummary');
        if (summaryNextGp) summaryNextGp.textContent = nextRace.race_name.replace(' Grand Prix', ' GP');
        if (summaryNextGpDate) summaryNextGpDate.textContent = formatDate(nextRace.date);

        if (!tbody) return;
        const predictions = nextRace.predictions || [];
        if (!predictions.length) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--muted); padding: 18px;">Nessuna stima disponibile per questo Gran Premio.</td></tr>';
            return;
        }

        tbody.innerHTML = predictions.map(p => {
            const rankClass = p.rank === 1 ? 'top-1' : p.rank === 2 ? 'top-2' : p.rank === 3 ? 'top-3' : '';
            const prob = Number(p.win_probability) || 0;
            const odds = Number(p.odds).toFixed(2);
            return `
                <tr>
                    <td style="text-align: center;">
                        <span class="f1-rank-badge ${rankClass}">${p.rank}</span>
                    </td>
                    <td>
                        <div class="f1-driver-cell">
                            <span class="f1-code-badge">${p.code || '---'}</span>
                            <span>${p.name}</span>
                        </div>
                    </td>
                    <td style="color: var(--muted);">${p.team || '—'}</td>
                    <td style="text-align: center; font-weight: 600;">${p.wins || 0}</td>
                    <td style="text-align: center; font-weight: 700;">${p.points || 0}</td>
                    <td style="text-align: center;">
                        <span class="f1-odds-badge" title="Quota modello">${odds}</span>
                    </td>
                    <td>
                        <div class="f1-prob-cell">
                            <div class="f1-prob-bar-wrap">
                                <div class="f1-prob-bar-bg">
                                    <div class="f1-prob-bar-fill" style="width: ${Math.min(100, prob)}%;"></div>
                                </div>
                                <span class="f1-prob-val">${prob.toFixed(1)}%</span>
                            </div>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    }

    function renderDriverStandings(standings) {
        const tbody = root.document.getElementById('f1DriversStandingsTbody');
        if (!tbody) return;
        if (!standings || !standings.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--muted); padding: 18px;">Classifica piloti non disponibile.</td></tr>';
            return;
        }

        tbody.innerHTML = standings.map(d => {
            const rankClass = d.position === 1 ? 'top-1' : d.position === 2 ? 'top-2' : d.position === 3 ? 'top-3' : '';
            return `
                <tr>
                    <td style="text-align: center;">
                        <span class="f1-rank-badge ${rankClass}">${d.position}</span>
                    </td>
                    <td>
                        <div class="f1-driver-cell">
                            <span class="f1-code-badge">${d.code || '---'}</span>
                            <span>${d.name}</span>
                        </div>
                    </td>
                    <td style="color: var(--muted);">${d.team || '—'}</td>
                    <td style="text-align: center; font-weight: 600;">${d.wins || 0}</td>
                    <td style="text-align: right; padding-right: 18px; font-weight: 700; color: #f2c879;">${d.points || 0}</td>
                </tr>
            `;
        }).join('');
    }

    function renderConstructorStandings(standings) {
        const tbody = root.document.getElementById('f1ConstructorsStandingsTbody');
        if (!tbody) return;
        if (!standings || !standings.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--muted); padding: 18px;">Classifica costruttori non disponibile.</td></tr>';
            return;
        }

        tbody.innerHTML = standings.map(c => {
            const rankClass = c.position === 1 ? 'top-1' : c.position === 2 ? 'top-2' : c.position === 3 ? 'top-3' : '';
            return `
                <tr>
                    <td style="text-align: center;">
                        <span class="f1-rank-badge ${rankClass}">${c.position}</span>
                    </td>
                    <td style="font-weight: 650;">${c.team}</td>
                    <td style="color: var(--muted);">${c.nationality || '—'}</td>
                    <td style="text-align: center; font-weight: 600;">${c.wins || 0}</td>
                    <td style="text-align: right; padding-right: 18px; font-weight: 700; color: #f2c879;">${c.points || 0}</td>
                </tr>
            `;
        }).join('');
    }

    function renderCalendar(calendar) {
        const tbody = root.document.getElementById('f1CalendarTbody');
        if (!tbody) return;
        if (!calendar || !calendar.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--muted); padding: 18px;">Calendario non disponibile.</td></tr>';
            return;
        }

        tbody.innerHTML = calendar.map(item => {
            let statusBadge = '<span class="formula1-badge upcoming">In programma</span>';
            if (item.status === 'completed') {
                statusBadge = '<span class="formula1-badge completed">🏁 Concluso</span>';
            } else if (item.status === 'next') {
                statusBadge = '<span class="formula1-badge next">⭐ Prossimo GP</span>';
            }

            let resultHtml = '<span style="color: var(--muted);">—</span>';
            if (item.winner) {
                resultHtml = `🏆 <strong>${item.winner.name}</strong> <span style="color: var(--muted); font-size: 11px;">(${item.winner.team})</span>`;
            } else if (item.status === 'next') {
                resultHtml = '<span style="color: #f2c879; font-weight: 600;">In arrivo</span>';
            }

            return `
                <tr ${item.status === 'next' ? 'style="background: #151e28;"' : ''}>
                    <td style="text-align: center; font-weight: 700; color: var(--muted);">R${item.round}</td>
                    <td style="color: var(--muted); font-size: 11.5px;">${formatDate(item.date)}</td>
                    <td style="font-weight: 650;">${item.race_name}</td>
                    <td style="color: var(--muted); font-size: 11px;">${item.circuit_name} (${item.country})</td>
                    <td style="text-align: center;">${statusBadge}</td>
                    <td>${resultHtml}</td>
                </tr>
            `;
        }).join('');
    }

    function renderDashboard(data) {
        cachedData = data;

        // Leader e statistiche
        const leaderEl = root.document.getElementById('f1LeaderSummary');
        const leaderPtsEl = root.document.getElementById('f1LeaderPointsSummary');
        if (data.leader) {
            if (leaderEl) leaderEl.textContent = data.leader.name;
            if (leaderPtsEl) leaderPtsEl.textContent = `${data.leader.points} pt (${data.leader.team})`;
        }

        const raceCountEl = root.document.getElementById('formula1RaceCount');
        const schedCountEl = root.document.getElementById('formula1ScheduleCount');
        if (raceCountEl) raceCountEl.textContent = String(data.completed_races ?? '—');
        if (schedCountEl) schedCountEl.textContent = `${data.completed_races} concluse / ${data.total_races} in calendario`;

        renderNextRacePredictions(data.next_race);
        renderDriverStandings(data.driver_standings);
        renderConstructorStandings(data.constructor_standings);
        renderCalendar(data.calendar);

        const leaderText = data.leader ? ` · Leader mondiale: ${data.leader.name} (${data.leader.points} pt)` : '';
        showStatus(`Dati Formula 1 aggiornati per la stagione ${data.season}${leaderText}.`);
    }

    async function loadFormula1ArchiveStatus() {
        try {
            const response = await root.fetch('/api/formula1/status', { cache: 'no-store' });
            if (!response.ok) return;
            const payload = await response.json();
            const seasonCount = Number(payload.season_count);
            const currentYear = Number(payload.end_year);
            const startYear = Number(payload.start_year);
            const lapEvents = Number(payload.lap_events_available);
            const featureRows = Number(payload.pre_race_feature_rows);

            const seasonCountEl = root.document.getElementById('formula1SeasonCount');
            const seasonRangeEl = root.document.getElementById('formula1SeasonRange');
            const lapCountEl = root.document.getElementById('formula1LapCount');
            const featCountEl = root.document.getElementById('formula1FeatureCount');
            const resultsDetailEl = root.document.getElementById('formula1ResultsDetail');

            if (seasonCountEl) seasonCountEl.textContent = Number.isFinite(seasonCount) ? String(seasonCount) : '—';
            if (seasonRangeEl && Number.isFinite(startYear) && Number.isFinite(currentYear)) {
                seasonRangeEl.textContent = `dal ${startYear} al ${currentYear}`;
            }
            if (lapCountEl) lapCountEl.textContent = Number.isFinite(lapEvents) ? String(lapEvents) : '—';
            if (featCountEl) featCountEl.textContent = `${featureRows} righe feature pre-gara`;
            if (resultsDetailEl) {
                resultsDetailEl.textContent = `${payload.qualifying_records} qualifiche · ${payload.driver_standings} piloti · ${payload.constructor_standings} team`;
            }
        } catch (_) {
            // Continua silenziosamente se i metadati opzionali non rispondono
        }
    }

    async function loadFormula1Data() {
        if (requestInProgress) return;
        requestInProgress = true;

        // Se abbiamo già dati incorporati (es. formula1-data.js), mostriamoli subito senza attendere la rete
        if (root.__FORMULA1_DATA__ && !cachedData) {
            renderDashboard(root.__FORMULA1_DATA__);
        } else if (!cachedData) {
            showStatus('Caricamento dati Formula 1 in corso…');
        }

        let data = null;

        // 1. Prova API dinamica backend
        try {
            const response = await root.fetch('/api/formula1/data', { cache: 'no-store' });
            if (response.ok) {
                data = await response.json();
            }
        } catch (_) {
            // Fallback
        }

        // 2. Se l'API non ha risposto, prova il file statico esportato
        if (!data) {
            try {
                const staticResp = await root.fetch('formula1/formula1-data.json', { cache: 'no-store' });
                if (staticResp.ok) {
                    data = await staticResp.json();
                }
            } catch (_) {
                // Fallback
            }
        }

        if (data) {
            renderDashboard(data);
            loadFormula1ArchiveStatus();
        } else if (root.__FORMULA1_DATA__) {
            renderDashboard(root.__FORMULA1_DATA__);
        } else if (!cachedData) {
            showStatus('Impossibile caricare i dati della Formula 1. Avvia avvia.bat e ricarica.', true);
        }

        requestInProgress = false;
    }

    async function triggerFormula1Refresh() {
        if (requestInProgress) return;
        requestInProgress = true;
        const refreshBtn = root.document.getElementById('formula1Refresh');
        if (refreshBtn) refreshBtn.disabled = true;
        showStatus('Aggiornamento dati Formula 1 in corso (download risultati recenti)…');

        try {
            const resp = await root.fetch('/api/formula1/refresh', { cache: 'no-store' });
            if (resp.ok) {
                const result = await resp.json();
                if (result.data) {
                    renderDashboard(result.data);
                    showStatus('Dati Formula 1 aggiornati con successo!');
                    loadFormula1ArchiveStatus();
                    requestInProgress = false;
                    if (refreshBtn) refreshBtn.disabled = false;
                    return;
                }
            }
        } catch (_) {}

        requestInProgress = false;
        await loadFormula1Data();
        if (refreshBtn) refreshBtn.disabled = false;
    }

    function setupStandingsTabs() {
        const btnDrivers = root.document.getElementById('f1BtnDriversStandings');
        const btnConstructors = root.document.getElementById('f1BtnConstructorsStandings');
        const wrapDrivers = root.document.getElementById('f1DriversStandingsWrap');
        const wrapConstructors = root.document.getElementById('f1ConstructorsStandingsWrap');

        if (!btnDrivers || !btnConstructors || !wrapDrivers || !wrapConstructors) return;

        btnDrivers.addEventListener('click', () => {
            btnDrivers.classList.add('active');
            btnDrivers.setAttribute('aria-selected', 'true');
            btnConstructors.classList.remove('active');
            btnConstructors.setAttribute('aria-selected', 'false');
            wrapDrivers.hidden = false;
            wrapConstructors.hidden = true;
        });

        btnConstructors.addEventListener('click', () => {
            btnConstructors.classList.add('active');
            btnConstructors.setAttribute('aria-selected', 'true');
            btnDrivers.classList.remove('active');
            btnDrivers.setAttribute('aria-selected', 'false');
            wrapDrivers.hidden = true;
            wrapConstructors.hidden = false;
        });
    }

    root.loadFormula1Data = loadFormula1Data;

    const f1Tab = root.document.getElementById('formula1Tab');
    const f1MobileTab = root.document.getElementById('formula1MobileTab');
    const f1Refresh = root.document.getElementById('formula1Refresh');

    if (f1Tab) f1Tab.addEventListener('click', loadFormula1Data);
    if (f1MobileTab) f1MobileTab.addEventListener('click', loadFormula1Data);
    if (f1Refresh) f1Refresh.addEventListener('click', triggerFormula1Refresh);

    setupStandingsTabs();

    if (root.document.readyState === 'loading') {
        root.document.addEventListener('DOMContentLoaded', loadFormula1Data, { once: true });
    } else {
        loadFormula1Data();
    }
})(typeof window === 'undefined' ? globalThis : window);
