(function initializeMotoGPView(root) {
    'use strict';

    const statusEl = root.document.getElementById('motogpStatus');
    let requestInProgress = false;
    let cachedData = null;
    let predictionMode = 'race';

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        })[character]);
    }

    function showStatus(message, isError = false) {
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.classList.toggle('is-error', Boolean(isError));
        statusEl.classList.remove('hidden');
    }

    function formatDate(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime())
            ? value
            : parsed.toLocaleDateString('it-IT', {
                day: 'numeric',
                month: 'short',
                year: 'numeric'
            });
    }

    function renderPredictions(nextRace) {
        const titleEl = root.document.getElementById('motogpNextRaceTitle');
        const circuitEl = root.document.getElementById('motogpCircuit');
        const locationEl = root.document.getElementById('motogpLocation');
        const dateEl = root.document.getElementById('motogpDate');
        const tbody = root.document.getElementById('motogpPredictionsTbody');

        if (!nextRace) {
            if (titleEl) titleEl.textContent = 'Stagione conclusa';
            if (circuitEl) circuitEl.textContent = '🏁 Nessun Gran Premio in calendario';
            if (locationEl) locationEl.textContent = '';
            if (dateEl) dateEl.textContent = '';
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:18px;">Tutti i Gran Premi della stagione sono stati completati.</td></tr>';
            }
            return;
        }

        if (titleEl) titleEl.textContent = nextRace.name || 'Gran Premio MotoGP';
        if (circuitEl) circuitEl.textContent = `🏁 ${nextRace.circuit || 'Circuito non specificato'}`;
        if (locationEl) {
            locationEl.textContent = `📍 ${[nextRace.place, nextRace.country].filter(Boolean).join(', ')}`;
        }
        if (dateEl) dateEl.textContent = `📅 ${formatDate(nextRace.date)}`;

        const raceMode = root.document.getElementById('motogpRaceMode');
        const sprintMode = root.document.getElementById('motogpSprintMode');
        if (raceMode) {
            raceMode.className = predictionMode === 'race'
                ? 'formula1-badge next'
                : 'formula1-badge upcoming';
        }
        if (sprintMode) {
            sprintMode.className = predictionMode === 'sprint'
                ? 'formula1-badge next'
                : 'formula1-badge upcoming';
        }

        const summaryName = root.document.getElementById('motogpNextRaceSummary');
        const summaryDate = root.document.getElementById('motogpNextRaceDateSummary');
        if (summaryName) summaryName.textContent = nextRace.name || 'Gran Premio MotoGP';
        if (summaryDate) summaryDate.textContent = formatDate(nextRace.date);
        if (!tbody) return;

        const predictions = predictionMode === 'sprint'
            ? (nextRace.sprint_predictions || [])
            : (nextRace.race_predictions || nextRace.predictions || []);
        if (!predictions.length) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:18px;">Stime non disponibili per questo Gran Premio.</td></tr>';
            return;
        }

        tbody.innerHTML = predictions.map(item => {
            const rankClass = item.rank <= 3 ? `top-${item.rank}` : '';
            const probability = Number(item.win_probability) || 0;
            return `
                <tr>
                    <td style="text-align:center;"><span class="f1-rank-badge ${rankClass}">${item.rank}</span></td>
                    <td><div class="f1-driver-cell"><span class="f1-code-badge">#${escapeHtml(item.number || '—')}</span><span>${escapeHtml(item.name)}</span></div></td>
                    <td style="color:var(--muted);">${escapeHtml(item.team || item.constructor || '—')}</td>
                    <td style="text-align:center;">${Number(item.race_wins || 0) + Number(item.sprint_wins || 0)}</td>
                    <td style="text-align:center;font-weight:700;">${escapeHtml(item.points)}</td>
                    <td style="text-align:center;"><span class="f1-odds-badge">${Number(item.odds).toFixed(2)}</span></td>
                    <td><div class="f1-prob-cell"><div class="f1-prob-bar-wrap"><div class="f1-prob-bar-bg"><div class="f1-prob-bar-fill" style="width:${Math.min(100, probability)}%;"></div></div><span class="f1-prob-val">${probability.toFixed(1)}%</span></div></div></td>
                </tr>
            `;
        }).join('');
    }

    function renderStandings(standings) {
        const tbody = root.document.getElementById('motogpStandingsTbody');
        if (!tbody) return;
        if (!standings || !standings.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:18px;">Classifica piloti non disponibile.</td></tr>';
            return;
        }
        tbody.innerHTML = standings.map(rider => {
            const rankClass = rider.position <= 3 ? `top-${rider.position}` : '';
            const wins = Number(rider.race_wins || 0) + Number(rider.sprint_wins || 0);
            return `
                <tr>
                    <td style="text-align:center;"><span class="f1-rank-badge ${rankClass}">${rider.position}</span></td>
                    <td><div class="f1-driver-cell"><span class="f1-code-badge">#${escapeHtml(rider.number || '—')}</span><span>${escapeHtml(rider.name)}</span></div></td>
                    <td style="color:var(--muted);">${escapeHtml(rider.team || rider.constructor || '—')}</td>
                    <td style="text-align:center;">${wins}</td>
                    <td style="text-align:right;padding-right:18px;font-weight:700;color:#ff9a84;">${escapeHtml(rider.points)}</td>
                </tr>
            `;
        }).join('');
    }

    function renderCalendar(calendar) {
        const tbody = root.document.getElementById('motogpCalendarTbody');
        if (!tbody) return;
        if (!calendar || !calendar.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:18px;">Calendario non disponibile.</td></tr>';
            return;
        }
        tbody.innerHTML = calendar.map((event, index) => {
            const badge = event.status === 'completed'
                ? '<span class="formula1-badge completed">Concluso</span>'
                : event.status === 'cancelled'
                    ? '<span class="formula1-badge upcoming">Annullato</span>'
                    : event.status === 'unavailable'
                        ? '<span class="formula1-badge upcoming">Risultato non disponibile</span>'
                    : index === calendar.findIndex(item => item.status === 'upcoming')
                        ? '<span class="formula1-badge next">Prossimo GP</span>'
                        : '<span class="formula1-badge upcoming">In programma</span>';
            const place = [event.circuit, event.place, event.country].filter(Boolean).join(' · ');
            const winner = event.winner
                ? `${escapeHtml(event.winner)}${event.winner_team ? ` <span style="color:var(--muted);">(${escapeHtml(event.winner_team)})</span>` : ''}`
                : '—';
            const sprintWinner = event.sprint_winner
                ? ` · Sprint: ${escapeHtml(event.sprint_winner)}`
                : '';
            return `
                <tr>
                    <td style="text-align:center;color:var(--muted);font-weight:700;">${index + 1}</td>
                    <td style="color:var(--muted);">${escapeHtml(formatDate(event.date))}</td>
                    <td style="font-weight:650;">${escapeHtml(event.name)}</td>
                    <td style="color:var(--muted);">${escapeHtml(place)}</td>
                    <td style="text-align:center;">${badge}</td>
                    <td>${winner}${sprintWinner}</td>
                </tr>
            `;
        }).join('');
    }

    function renderDashboard(data) {
        cachedData = data;
        const seasonEl = root.document.getElementById('motogpSeasonEyebrow');
        const raceCountEl = root.document.getElementById('motogpRaceCount');
        const scheduleCountEl = root.document.getElementById('motogpScheduleCount');
        const leaderEl = root.document.getElementById('motogpLeaderSummary');
        const leaderPointsEl = root.document.getElementById('motogpLeaderPointsSummary');
        if (seasonEl) seasonEl.textContent = String(data.season || '—');
        if (raceCountEl) raceCountEl.textContent = String(data.completed_races ?? '—');
        if (scheduleCountEl) {
            scheduleCountEl.textContent = `${data.completed_races || 0} concluse / ${data.total_races || 0} in calendario`;
        }
        if (leaderEl) leaderEl.textContent = data.leader?.name || '—';
        if (leaderPointsEl) {
            leaderPointsEl.textContent = data.leader
                ? `${data.leader.points} pt · ${data.leader.team || data.leader.constructor || 'Team non indicato'}`
                : 'classifica piloti';
        }
        renderPredictions(data.next_race);
        renderStandings(data.standings);
        renderCalendar(data.calendar);
        const leader = data.leader ? ` · Leader: ${data.leader.name} (${data.leader.points} pt)` : '';
        showStatus(`Dati MotoGP aggiornati per la stagione ${data.season}${leader}.`);
    }

    async function loadArchiveStatus() {
        try {
            const response = await root.fetch('/api/motogp/status', { cache: 'no-store' });
            if (!response.ok) return;
            const payload = await response.json();
            const seasonCount = Number(payload.season_count);
            const currentYear = Number(payload.end_year);
            const startYear = Number(payload.start_year);
            const seasonCountEl = root.document.getElementById('motogpSeasonCount');
            const rangeEl = root.document.getElementById('motogpSeasonRange');
            const sprintEl = root.document.getElementById('motogpSprintCount');
            const featureEl = root.document.getElementById('motogpFeatureCount');
            const resultsEl = root.document.getElementById('motogpResultsDetail');
            if (seasonCountEl && Number.isFinite(seasonCount)) seasonCountEl.textContent = String(seasonCount);
            if (rangeEl && Number.isFinite(startYear) && Number.isFinite(currentYear)) {
                rangeEl.textContent = `dal ${startYear} al ${currentYear}`;
            }
            if (sprintEl) {
                sprintEl.textContent = `${payload.completed_races || 0} / ${payload.completed_sprints || 0}`;
            }
            if (featureEl) featureEl.textContent = `${payload.pre_race_feature_rows || 0} righe feature pre-gara`;
            if (resultsEl) resultsEl.textContent = `${payload.scheduled_events || 0} eventi · ${payload.unavailable_count || 0} classifiche mancanti`;
        } catch (error) {
            showStatus(`Impossibile caricare lo stato dell'archivio MotoGP: ${error.message}`, true);
        }
    }

    async function loadMotoGPData() {
        if (requestInProgress) return;
        requestInProgress = true;
        if (root.__MOTOGP_DATA__ && !cachedData) {
            renderDashboard(root.__MOTOGP_DATA__);
        } else if (!cachedData) {
            showStatus('Caricamento dati MotoGP in corso…');
        }

        let data = null;
        let requestError = '';
        try {
            const response = await root.fetch('/api/motogp/data', { cache: 'no-store' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'API MotoGP non disponibile.');
            data = payload;
        } catch (error) {
            requestError = error.message;
        }

        if (!data) {
            try {
                const response = await root.fetch('motogp/motogp-data.json', { cache: 'no-store' });
                if (response.ok) data = await response.json();
            } catch (error) {
                requestError = requestError || error.message;
            }
        }

        if (data) {
            renderDashboard(data);
            loadArchiveStatus();
        } else if (root.__MOTOGP_DATA__) {
            renderDashboard(root.__MOTOGP_DATA__);
        } else if (!cachedData) {
            showStatus(
                `Archivio MotoGP non disponibile. Apri aggiorna_database.bat per scaricarlo.${requestError ? ` (${requestError})` : ''}`,
                true
            );
        }
        requestInProgress = false;
    }

    async function triggerRefresh() {
        if (requestInProgress) return;
        requestInProgress = true;
        const button = root.document.getElementById('motogpRefresh');
        if (button) button.disabled = true;
        showStatus('Aggiornamento della stagione MotoGP in corso…');
        try {
            const response = await root.fetch('/api/motogp/refresh', { cache: 'no-store' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Aggiornamento MotoGP non riuscito.');
            renderDashboard(result.data);
            loadArchiveStatus();
        } catch (error) {
            showStatus(`Aggiornamento MotoGP non riuscito: ${error.message}`, true);
        } finally {
            requestInProgress = false;
            if (button) button.disabled = false;
        }
    }

    const refreshButton = root.document.getElementById('motogpRefresh');
    if (refreshButton) refreshButton.addEventListener('click', triggerRefresh);
    const raceModeButton = root.document.getElementById('motogpRaceMode');
    const sprintModeButton = root.document.getElementById('motogpSprintMode');
    if (raceModeButton) {
        raceModeButton.addEventListener('click', () => {
            predictionMode = 'race';
            if (cachedData?.next_race) renderPredictions(cachedData.next_race);
        });
    }
    if (sprintModeButton) {
        sprintModeButton.addEventListener('click', () => {
            predictionMode = 'sprint';
            if (cachedData?.next_race) renderPredictions(cachedData.next_race);
        });
    }
    root.loadMotoGPData = loadMotoGPData;
}(window));
