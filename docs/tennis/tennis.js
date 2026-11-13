(function (root) {
    'use strict';

    const SHRINKAGE_POINTS = 150;
    const MAX_FATIGUE_SHIFT = 0.02;

    function normalizeName(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLocaleLowerCase('en')
            .replace(/[^a-z0-9]/g, '');
    }

    function parseCsv(text) {
        const rows = [];
        let row = [];
        let field = '';
        let quoted = false;

        for (let index = 0; index < text.length; index += 1) {
            const character = text[index];
            if (quoted) {
                if (character === '"' && text[index + 1] === '"') {
                    field += '"';
                    index += 1;
                } else if (character === '"') {
                    quoted = false;
                } else {
                    field += character;
                }
            } else if (character === '"' && field.length === 0) {
                quoted = true;
            } else if (character === ',') {
                row.push(field);
                field = '';
            } else if (character === '\n' || character === '\r') {
                row.push(field);
                field = '';
                if (row.some(value => value.trim() !== '')) rows.push(row);
                row = [];
                if (character === '\r' && text[index + 1] === '\n') index += 1;
            } else {
                field += character;
            }
        }

        if (quoted) throw new Error('Il CSV contiene un campo tra virgolette non chiuso.');
        if (field.length || row.length) {
            row.push(field);
            if (row.some(value => value.trim() !== '')) rows.push(row);
        }
        if (rows.length < 2) throw new Error('Il CSV non contiene righe di match.');

        const headers = rows[0].map((header, index) =>
            (index === 0 ? header.replace(/^\uFEFF/, '') : header)
                .trim()
                .toLowerCase()
                .replace(/[^a-z0-9]/g, '')
        );
        if (new Set(headers).size !== headers.length) {
            throw new Error('Il CSV contiene intestazioni duplicate dopo la normalizzazione.');
        }

        return rows.slice(1).map(values => {
            const record = {};
            headers.forEach((header, index) => {
                record[header] = (values[index] || '').trim();
            });
            return record;
        });
    }

    function readNumber(record, key) {
        const value = Number(record[key]);
        return record[key] !== undefined && record[key] !== '' && Number.isFinite(value) && value >= 0
            ? value
            : null;
    }

    function normalizeSurface(value) {
        const surface = String(value || '').trim().toLowerCase();
        if (surface === 'clay' || surface === 'terra' || surface === 'terra battuta') return 'clay';
        if (surface === 'grass' || surface === 'erba') return 'grass';
        if (/^(hard|cement|cemento)\s*(indoor|in)\b/.test(surface) ||
            /^(indoor)\s*(hard|cement|cemento)\b/.test(surface)) return 'hard_indoor';
        if (/^(hard|cement|cemento)\s*(outdoor|out)\b/.test(surface) ||
            /^(outdoor)\s*(hard|cement|cemento)\b/.test(surface)) return 'hard_outdoor';
        if (surface === 'hard' || surface === 'cement' || surface === 'cemento') return 'hard_unspecified';
        return null;
    }

    function parseDate(value) {
        const raw = String(value || '').trim();
        if (/^\d{8}$/.test(raw)) {
            const year = Number(raw.slice(0, 4));
            const month = Number(raw.slice(4, 6)) - 1;
            const day = Number(raw.slice(6, 8));
            const date = new Date(Date.UTC(year, month, day));
            return date.getUTCFullYear() === year && date.getUTCMonth() === month &&
                date.getUTCDate() === day ? date : null;
        }
        const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!match) return null;
        const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
        return date.getUTCFullYear() === Number(match[1]) &&
            date.getUTCMonth() === Number(match[2]) - 1 &&
            date.getUTCDate() === Number(match[3]) ? date : null;
    }

    function getServiceStats(record, side) {
        const servicePoints = readNumber(record, `${side}svpt`);
        const firstWon = readNumber(record, `${side}1stwon`);
        const secondWon = readNumber(record, `${side}2ndwon`);
        const servicePointsWon = firstWon === null || secondWon === null ? null : firstWon + secondWon;
        const breakPointsSaved = readNumber(record, `${side}bpsaved`);
        const breakPointsFaced = readNumber(record, `${side}bpfaced`);

        if (servicePointsWon !== null && servicePoints !== null && servicePointsWon > servicePoints) {
            throw new Error('Il CSV contiene punti vinti al servizio superiori ai punti giocati.');
        }
        if (breakPointsSaved !== null && breakPointsFaced !== null &&
            breakPointsSaved > breakPointsFaced) {
            throw new Error('Il CSV contiene break point salvati superiori a quelli affrontati.');
        }

        return {
            servicePoints,
            servicePointsWon,
            breakPointsSaved,
            breakPointsFaced,
        };
    }

    function parseMatches(text) {
        const rows = parseCsv(text);
        const required = ['surface', 'winnername', 'losername'];
        const headers = new Set(Object.keys(rows[0] || {}));
        const missing = required.filter(header => !headers.has(header));
        if (missing.length) {
            throw new Error(`Colonne obbligatorie mancanti: ${missing.join(', ')}.`);
        }

        const matches = [];
        for (const row of rows) {
            const winnerName = row.winnername;
            const loserName = row.losername;
            if (!winnerName || !loserName || normalizeName(winnerName) === normalizeName(loserName)) continue;

            const winnerService = getServiceStats(row, 'w');
            const loserService = getServiceStats(row, 'l');
            const winnerReturnPoints = loserService.servicePoints !== null &&
                loserService.servicePointsWon !== null
                ? loserService.servicePoints - loserService.servicePointsWon
                : null;
            const loserReturnPoints = winnerService.servicePoints !== null &&
                winnerService.servicePointsWon !== null
                ? winnerService.servicePoints - winnerService.servicePointsWon
                : null;
            const score = String(row.score || '');
            const tieBreaks = (score.match(/\d+\s*-\s*\d+(?:\(\d+\))?/g) || [])
                .map(set => set.match(/(\d+)\s*-\s*(\d+)/))
                .filter(set => set && ((Number(set[1]) === 7 && Number(set[2]) === 6) ||
                    (Number(set[1]) === 6 && Number(set[2]) === 7)))
                .map(set => Number(set[1]) === 7);
            const minutes = readNumber(row, 'minutes');

            matches.push({
                winnerName,
                loserName,
                winnerKey: normalizeName(winnerName),
                loserKey: normalizeName(loserName),
                bestOf: readNumber(row, 'bestof'),
                surface: normalizeSurface(row.surface),
                date: parseDate(row.tourneydate || row.date),
                minutes,
                tournament: row.tourneyname || '',
                score,
                winner: {
                    servicePoints: winnerService.servicePoints,
                    servicePointsWon: winnerService.servicePointsWon,
                    returnPoints: winnerReturnPoints,
                    returnPointsTotal: loserService.servicePoints,
                    breakPointsSaved: winnerService.breakPointsSaved,
                    breakPointsFaced: winnerService.breakPointsFaced,
                    tieBreaks: tieBreaks.length,
                    tieBreaksWon: tieBreaks.filter(Boolean).length,
                },
                loser: {
                    servicePoints: loserService.servicePoints,
                    servicePointsWon: loserService.servicePointsWon,
                    returnPoints: loserReturnPoints,
                    returnPointsTotal: winnerService.servicePoints,
                    breakPointsSaved: loserService.breakPointsSaved,
                    breakPointsFaced: loserService.breakPointsFaced,
                    tieBreaks: tieBreaks.length,
                    tieBreaksWon: tieBreaks.filter(value => !value).length,
                },
            });
        }
        if (!matches.length) throw new Error('Non sono stati trovati match validi nel CSV.');
        return matches;
    }

    function emptyStats() {
        return {
            matches: 0,
            wins: 0,
            servicePoints: 0,
            servicePointsWon: 0,
            returnPoints: 0,
            returnPointsTotal: 0,
            breakPointsSaved: 0,
            breakPointsFaced: 0,
            breakPointsConverted: 0,
            breakPointsConversionChances: 0,
            tieBreaks: 0,
            tieBreaksWon: 0,
        };
    }

    function addPlayerMatch(stats, player, opponent) {
        stats.matches += 1;
        if (player.servicePoints !== null && player.servicePointsWon !== null) {
            stats.servicePoints += player.servicePoints;
            stats.servicePointsWon += player.servicePointsWon;
        }
        if (player.returnPoints !== null && player.returnPointsTotal !== null) {
            stats.returnPoints += player.returnPoints;
            stats.returnPointsTotal += player.returnPointsTotal;
        }
        if (player.breakPointsSaved !== null && player.breakPointsFaced !== null) {
            stats.breakPointsSaved += player.breakPointsSaved;
            stats.breakPointsFaced += player.breakPointsFaced;
        }
        if (opponent.breakPointsSaved !== null && opponent.breakPointsFaced !== null) {
            stats.breakPointsConverted += Math.max(0, opponent.breakPointsFaced - opponent.breakPointsSaved);
            stats.breakPointsConversionChances += opponent.breakPointsFaced;
        }
        stats.tieBreaks += player.tieBreaks;
        stats.tieBreaksWon += player.tieBreaksWon;
    }

    function holdProbability(pointWinProbability) {
        const p = Math.min(0.999999, Math.max(0.000001, pointWinProbability));
        const q = 1 - p;
        return p ** 4 * (1 + 4 * q + 10 * q ** 2) +
            20 * p ** 3 * q ** 3 * (p ** 2 / (p ** 2 + q ** 2));
    }

    function summarizeSurface(matches, surface) {
        const selected = matches.filter(match => match.surface === surface);
        const players = new Map();
        const total = {
            servicePoints: 0,
            servicePointsWon: 0,
            returnPoints: 0,
            returnPointsTotal: 0,
        };
        const getPlayer = (name, key) => {
            if (!players.has(key)) {
                players.set(key, { name, key, ...emptyStats() });
            }
            return players.get(key);
        };

        selected.forEach(match => {
            const winner = getPlayer(match.winnerName, match.winnerKey);
            const loser = getPlayer(match.loserName, match.loserKey);
            winner.wins += 1;
            addPlayerMatch(winner, match.winner, match.loser);
            addPlayerMatch(loser, match.loser, match.winner);
            for (const player of [match.winner, match.loser]) {
                if (player.servicePoints !== null && player.servicePointsWon !== null) {
                    total.servicePoints += player.servicePoints;
                    total.servicePointsWon += player.servicePointsWon;
                }
                if (player.returnPoints !== null && player.returnPointsTotal !== null) {
                    total.returnPoints += player.returnPoints;
                    total.returnPointsTotal += player.returnPointsTotal;
                }
            }
        });

        const globalServe = total.servicePoints
            ? total.servicePointsWon / total.servicePoints
            : null;
        const globalReturn = total.returnPointsTotal
            ? total.returnPoints / total.returnPointsTotal
            : null;

        for (const player of players.values()) {
            player.servePointRate = globalServe === null || !player.servicePoints
                ? null
                : (player.servicePointsWon + SHRINKAGE_POINTS * globalServe) /
                    (player.servicePoints + SHRINKAGE_POINTS);
            player.returnPointRate = globalReturn === null || !player.returnPointsTotal
                ? null
                : (player.returnPoints + SHRINKAGE_POINTS * globalReturn) /
                    (player.returnPointsTotal + SHRINKAGE_POINTS);
            player.holdRate = player.servePointRate === null
                ? null
                : holdProbability(player.servePointRate);
            player.breakRate = player.returnPointRate === null
                ? null
                : 1 - holdProbability(1 - player.returnPointRate);
            player.winRate = player.matches ? player.wins / player.matches : null;
            player.breakPointSaveRate = player.breakPointsFaced
                ? player.breakPointsSaved / player.breakPointsFaced
                : null;
            player.breakPointConversionRate = player.breakPointsConversionChances
                ? player.breakPointsConverted / player.breakPointsConversionChances
                : null;
            player.clutchFactor = player.breakPointSaveRate === null ||
                player.breakPointConversionRate === null
                ? null
                : (player.breakPointSaveRate + player.breakPointConversionRate) / 2;
            player.tieBreakWinRate = player.tieBreaks
                ? player.tieBreaksWon / player.tieBreaks
                : null;
        }
        return { matches: selected, players, globalServe, globalReturn };
    }

    function surfaceDisplayName(surface) {
        return ({
            clay: 'terra battuta',
            grass: 'erba',
            hard_indoor: 'cemento indoor',
            hard_outdoor: 'cemento outdoor',
            hard_unspecified: 'cemento non specificato',
        })[surface] || surface;
    }

    function playerSurfaceCounts(matches, playerName) {
        const playerKey = normalizeName(playerName);
        const counts = new Map();
        matches.forEach(match => {
            if (match.winnerKey !== playerKey && match.loserKey !== playerKey) return;
            if (!match.surface) return;
            counts.set(match.surface, (counts.get(match.surface) || 0) + 1);
        });
        return counts;
    }

    function recentLoad(matches, playerKey, referenceDate) {
        const reference = Date.UTC(
            referenceDate.getUTCFullYear(),
            referenceDate.getUTCMonth(),
            referenceDate.getUTCDate()
        );
        const start = reference - 21 * 24 * 60 * 60 * 1000;
        const recent = matches.filter(match => {
            const participated = match.winnerKey === playerKey || match.loserKey === playerKey;
            const date = match.date && match.date.getTime();
            return participated && date !== null && date !== undefined && date < reference && date >= start;
        });
        return {
            matches: recent.length,
            minutes: recent.reduce((sum, match) => sum + (match.minutes || 0), 0),
        };
    }

    function headToHead(surfaceMatches, playerA, playerB) {
        const h2h = surfaceMatches.filter(match =>
            (match.winnerKey === playerA.key && match.loserKey === playerB.key) ||
            (match.winnerKey === playerB.key && match.loserKey === playerA.key)
        );
        return {
            matches: h2h.length,
            winsA: h2h.filter(match => match.winnerKey === playerA.key).length,
            winsB: h2h.filter(match => match.winnerKey === playerB.key).length,
        };
    }

    function tieBreakWinProbability(pointA, pointB, firstServerA) {
        const p = (aServes) => aServes ? pointA : 1 - pointB;
        const probabilityAOnTwoPoints = p(!firstServerA) * p(firstServerA);
        const probabilityBOnTwoPoints = (1 - p(!firstServerA)) * (1 - p(firstServerA));
        const deuceWinA = probabilityAOnTwoPoints /
            (probabilityAOnTwoPoints + probabilityBOnTwoPoints);
        const memo = new Map();

        function visit(a, b, point) {
            if (a >= 7 && a - b >= 2) return 1;
            if (b >= 7 && b - a >= 2) return 0;
            if (a >= 6 && b >= 6) return deuceWinA;
            const key = `${a}:${b}:${point}`;
            if (memo.has(key)) return memo.get(key);
            const serverA = point === 0
                ? firstServerA
                : ((Math.floor((point - 1) / 2) % 2 === 0) ? !firstServerA : firstServerA);
            const winPointA = p(serverA);
            const result = winPointA * visit(a + 1, b, point + 1) +
                (1 - winPointA) * visit(a, b + 1, point + 1);
            memo.set(key, result);
            return result;
        }
        return visit(0, 0, 0);
    }

    function getSetDistribution(pointA, pointB, firstServerA) {
        const holdA = holdProbability(pointA);
        const holdB = holdProbability(pointB);
        const memo = new Map();

        function play(a, b, serverA) {
            if (a === 6 && b === 6) {
                const tieBreakA = tieBreakWinProbability(pointA, pointB, firstServerA);
                return [
                    { winnerA: true, games: 13, tieBreak: true, probability: tieBreakA },
                    { winnerA: false, games: 13, tieBreak: true, probability: 1 - tieBreakA },
                ];
            }
            if ((a >= 6 || b >= 6) && Math.abs(a - b) >= 2) {
                return [{ winnerA: a > b, games: a + b, tieBreak: false, probability: 1 }];
            }
            const key = `${a}:${b}:${serverA ? 1 : 0}`;
            if (memo.has(key)) return memo.get(key);
            const gameA = serverA ? holdA : 1 - holdB;
            const outcomes = [];
            for (const outcome of play(a + 1, b, !serverA)) {
                outcomes.push({ ...outcome, probability: outcome.probability * gameA });
            }
            for (const outcome of play(a, b + 1, !serverA)) {
                outcomes.push({ ...outcome, probability: outcome.probability * (1 - gameA) });
            }
            memo.set(key, outcomes);
            return outcomes;
        }
        return play(0, 0, firstServerA);
    }

    function simulateMatch(pointA, pointB, bestOf, line) {
        const setsToWin = Math.ceil(bestOf / 2);
        let states = new Map([['0:0:0:0', {
            setsA: 0, setsB: 0, games: 0, tieBreak: false, probability: 1,
        }]]);
        const finals = [];
        const setDistributions = new Map();

        for (let setIndex = 0; setIndex < bestOf; setIndex += 1) {
            const next = new Map();
            for (const state of states.values()) {
                if (state.setsA === setsToWin || state.setsB === setsToWin) {
                    finals.push(state);
                    continue;
                }
                const firstServerA = setIndex % 2 === 0;
                const setKey = firstServerA ? 'A' : 'B';
                if (!setDistributions.has(setKey)) {
                    setDistributions.set(setKey, getSetDistribution(pointA, pointB, firstServerA));
                }
                for (const set of setDistributions.get(setKey)) {
                    const updated = {
                        setsA: state.setsA + (set.winnerA ? 1 : 0),
                        setsB: state.setsB + (set.winnerA ? 0 : 1),
                        games: state.games + set.games,
                        tieBreak: state.tieBreak || set.tieBreak,
                        probability: state.probability * set.probability,
                    };
                    const key = `${updated.setsA}:${updated.setsB}:${updated.games}:${updated.tieBreak ? 1 : 0}`;
                    const previous = next.get(key);
                    if (previous) previous.probability += updated.probability;
                    else next.set(key, updated);
                }
            }
            states = next;
        }
        finals.push(...states.values());

        const result = {
            winA: 0,
            winB: 0,
            expectedGames: 0,
            over: 0,
            under: 0,
            push: 0,
            atLeastOneTieBreak: 0,
            setScores: {},
            totalProbability: 0,
        };
        finals.forEach(state => {
            const winnerA = state.setsA > state.setsB;
            const probability = state.probability;
            const score = `${state.setsA}-${state.setsB}`;
            result.totalProbability += probability;
            result[winnerA ? 'winA' : 'winB'] += probability;
            result.expectedGames += state.games * probability;
            result[state.games > line ? 'over' : state.games < line ? 'under' : 'push'] += probability;
            if (state.tieBreak) result.atLeastOneTieBreak += probability;
            result.setScores[score] = (result.setScores[score] || 0) + probability;
        });
        return result;
    }

    function analyzeMatch(matches, options) {
        if (!['3', '5', 3, 5].includes(options.bestOf)) {
            throw new Error('Il formato del match deve essere al meglio dei 3 o dei 5 set.');
        }
        const surface = summarizeSurface(matches, options.surface);
        const playerA = surface.players.get(normalizeName(options.playerA));
        const playerB = surface.players.get(normalizeName(options.playerB));
        if (!playerA || !playerB) {
            const missingPlayers = [
                { name: options.playerA, player: playerA },
                { name: options.playerB, player: playerB },
            ].filter(entry => !entry.player);
            const details = missingPlayers.map(({ name }) => {
                const counts = playerSurfaceCounts(matches, name);
                if (!counts.size) {
                    return `${name}: nessun match nello storico caricato o nome non riconosciuto`;
                }
                const available = Array.from(counts.entries())
                    .sort((left, right) => right[1] - left[1])
                    .map(([availableSurface, count]) =>
                        `${surfaceDisplayName(availableSurface)} (${count})`
                    )
                    .join(', ');
                return `${name}: storico disponibile su ${available}`;
            });
            throw new Error(
                `Nessun match storico per uno o entrambi i giocatori su ${surfaceDisplayName(options.surface)}. ` +
                `${details.join('; ')}. Controlla i nomi e seleziona la superficie reale del match; ` +
                'le superfici non vengono sostituite automaticamente.'
            );
        }
        if (playerA.servePointRate === null || playerB.servePointRate === null ||
            playerA.returnPointRate === null || playerB.returnPointRate === null) {
            throw new Error('Servono statistiche aggregate dei punti al servizio per entrambi i giocatori su questa superficie.');
        }

        const referenceDate = parseDate(options.matchDate);
        if (!referenceDate) throw new Error('Inserisci una data del match valida.');
        const recentA = recentLoad(matches, playerA.key, referenceDate);
        const recentB = recentLoad(matches, playerB.key, referenceDate);
        const tournamentMinutesA = Number(options.tournamentMinutesA) || 0;
        const tournamentMinutesB = Number(options.tournamentMinutesB) || 0;
        const burdenA = recentA.minutes + recentA.matches * 45 + tournamentMinutesA;
        const burdenB = recentB.minutes + recentB.matches * 45 + tournamentMinutesB;
        const fatigueShift = MAX_FATIGUE_SHIFT *
            Math.tanh((burdenB - burdenA) / 360);

        let pointA = (playerA.servePointRate + (1 - playerB.returnPointRate)) / 2;
        let pointB = (playerB.servePointRate + (1 - playerA.returnPointRate)) / 2;
        pointA = Math.min(0.9, Math.max(0.1, pointA - fatigueShift));
        pointB = Math.min(0.9, Math.max(0.1, pointB + fatigueShift));

        const line = Number(options.gameLine);
        if (!Number.isFinite(line) || line < 0 || line > 200) {
            throw new Error('La linea game deve essere un numero valido tra 0 e 200.');
        }
        const prediction = simulateMatch(pointA, pointB, Number(options.bestOf), line);
        const h2h = headToHead(surface.matches, playerA, playerB);
        return {
            surface,
            playerA,
            playerB,
            recentA,
            recentB,
            tournamentMinutesA,
            tournamentMinutesB,
            fatigueShift,
            pointA,
            pointB,
            line,
            prediction,
            h2h,
            bestOf: Number(options.bestOf),
        };
    }

    const model = {
        analyzeMatch,
        holdProbability,
        normalizeName,
        normalizeSurface,
        parseCsv,
        parseMatches,
        simulateMatch,
        summarizeSurface,
    };

    root.TennisModel = model;
    if (typeof module !== 'undefined' && module.exports) module.exports = model;
    if (!root.document) return;

    let importedMatches = [];
    let csvMatches = [];
    let historyMatches = [];
    let scrapedTournamentMatches = [];
    const historyByTour = new Map();
    const historyRequests = new Map();
    const calendarMatchesById = new Map();
    let activeTour = 'atp';

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, character => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        })[character]);
    }

    function setDataStatus(message, isError) {
        const status = root.document.getElementById('tennisDataStatus');
        status.textContent = message;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function setScrapeStatus(message, isError) {
        const status = root.document.getElementById('tennisScrapeStatus');
        status.textContent = message;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function setHistoryStatus(message, isError) {
        const status = root.document.getElementById('tennisHistoryStatus');
        status.textContent = message;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function setCalendarStatus(message, isError) {
        const status = root.document.getElementById('tennisCalendarStatus');
        status.textContent = message;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function apiResponseError(response, body, fallback) {
        try {
            const payload = JSON.parse(body);
            if (payload && typeof payload.error === 'string') return payload.error;
        } catch (error) {
            if (!(error instanceof SyntaxError)) throw error;
        }

        const detail = body.trim().slice(0, 200);
        const responseDetail = detail
            ? `HTTP ${response.status}: ${detail}`
            : `HTTP ${response.status}, risposta vuota`;
        return `${fallback} (${responseDetail}). Avvia il server con avvia.bat e apri ` +
            'http://localhost:8000/docs/.';
    }

    async function readApiJson(response, fallback) {
        const body = await response.text();
        let payload;
        try {
            payload = JSON.parse(body);
        } catch (error) {
            if (!(error instanceof SyntaxError)) throw error;
            throw new Error(apiResponseError(response, body, fallback));
        }
        if (!response.ok) {
            throw new Error(
                payload && typeof payload.error === 'string'
                    ? payload.error
                    : apiResponseError(response, body, fallback)
            );
        }
        return payload;
    }

    function apiFailureMessage(error) {
        const detail = error.message || 'Errore di rete non specificato.';
        if (!(error instanceof TypeError)) return detail;
        return `${detail}. Avvia il server con avvia.bat e apri http://localhost:8000/docs/.`;
    }

    function surfaceLabel(surface) {
        if (surface === 'hard_unspecified') {
            return 'cemento (indoor/outdoor non specificato)';
        }
        return surfaceDisplayName(surface) || 'da confermare';
    }

    function tournamentKey(value) {
        return normalizeName(value)
            .replace(/(atp|wta|tennis|open|masters|championship|tournament|international|presented|by)$/g, '');
    }

    function inferTournamentSurface(tournament, matches) {
        const targetKey = tournamentKey(tournament);
        const targetTokens = String(tournament || '').toLowerCase()
            .split(/[^a-z0-9]+/)
            .filter(token => token.length > 3 &&
                !['tennis', 'open', 'masters', 'championship', 'tournament', 'presented'].includes(token));
        if (!targetKey) return null;

        const groups = new Map();
        matches.forEach(match => {
            if (!match.tournament || !match.date || !match.surface) return;
            const historyKey = tournamentKey(match.tournament);
            const historyTokens = match.tournament.toLowerCase()
                .split(/[^a-z0-9]+/)
                .filter(token => token.length > 3 &&
                    !['tennis', 'open', 'masters', 'championship', 'tournament', 'presented'].includes(token));
            const commonTokens = targetTokens.filter(token => historyTokens.includes(token)).length;
            const exact = targetKey === historyKey;
            const contained = Math.min(targetKey.length, historyKey.length) >= 7 &&
                (targetKey.includes(historyKey) || historyKey.includes(targetKey));
            const union = new Set([...targetTokens, ...historyTokens]).size;
            const overlap = union ? commonTokens / union : 0;
            if (!exact && !contained && !(commonTokens >= 2 && overlap >= 0.6)) return;

            const score = exact ? 3 : contained ? 2 : overlap;
            const year = match.date.getUTCFullYear();
            const key = `${match.tournament}|${year}`;
            let group = groups.get(key);
            if (!group || score > group.score) {
                group = { score, year, matches: [] };
                groups.set(key, group);
            }
            if (score === group.score) group.matches.push(match);
        });

        const best = Array.from(groups.values()).sort((left, right) =>
            right.score - left.score || right.year - left.year
        )[0];
        if (!best || best.matches.length < 3) return null;
        const counts = new Map();
        best.matches.forEach(match => counts.set(
            match.surface,
            (counts.get(match.surface) || 0) + 1
        ));
        const [surface, count] = Array.from(counts.entries())
            .sort((left, right) => right[1] - left[1])[0] || [];
        return surface && count / best.matches.length >= 0.7 ? surface : null;
    }

    function renderCalendar(matches) {
        const container = root.document.getElementById('tennisCalendarMatches');
        calendarMatchesById.clear();
        if (!matches.length) {
            container.innerHTML = '<p class="tennis-output-detail">Nessun singolare con due giocatori confermati nel calendario dei prossimi 14 giorni.</p>';
            return;
        }

        matches.forEach(match => {
            match.surface = inferTournamentSurface(match.tournament, historyMatches);
            calendarMatchesById.set(match.id, match);
        });
        container.innerHTML = matches.map(match => {
            const date = new Date(match.date);
            const dateLabel = Number.isNaN(date.getTime())
                ? 'Orario da confermare'
                : date.toLocaleString('it-IT', {
                    weekday: 'short',
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                });
            const venue = [match.venue, match.court].filter(Boolean).join(' · ');
            return `
                <article class="tennis-calendar-card">
                    <h3>${escapeHtml(match.tournament)}</h3>
                    <p>${escapeHtml(match.tour)} · ${escapeHtml(dateLabel)}</p>
                    <p class="tennis-calendar-players">${escapeHtml(match.playerA)} – ${escapeHtml(match.playerB)}</p>
                    ${venue ? `<p>${escapeHtml(venue)}</p>` : ''}
                    <p>${escapeHtml(surfaceLabel(match.surface))} · al meglio dei ${match.bestOf}</p>
                    <button class="action-button secondary" type="button" data-tennis-match="${escapeHtml(match.id)}">Pronostica questo match</button>
                </article>
            `;
        }).join('');
    }

    async function loadHistory(tour, force) {
        if (!force && historyByTour.has(tour)) {
            historyMatches = historyByTour.get(tour);
            activeTour = tour;
            importedMatches = mergeMatchSources([
                historyMatches,
                csvMatches,
                scrapedTournamentMatches,
            ]);
            renderPlayers(importedMatches);
            renderCalendar(Array.from(calendarMatchesById.values()));
            return;
        }
        if (historyRequests.has(tour)) return historyRequests.get(tour);

        const currentYear = new Date().getFullYear();
        const startYear = Math.max(1968, currentYear - 4);
        const params = new URLSearchParams({
            tour,
            start: String(startYear),
            end: String(currentYear),
        });
        setHistoryStatus(`Scarico lo storico ${tour.toUpperCase()} ${startYear}–${currentYear}…`);
        const request = (async () => {
            const response = await fetch(`/api/tennis-history?${params.toString()}`, {
                cache: 'no-store',
            });
            if (!response.ok) {
                const body = await response.text();
                throw new Error(apiResponseError(
                    response,
                    body,
                    'Impossibile scaricare lo storico'
                ));
            }
            const contentType = response.headers.get('Content-Type') || '';
            if (!contentType.toLowerCase().includes('text/csv')) {
                const body = await response.text();
                throw new Error(apiResponseError(
                    response,
                    body,
                    'Il server non ha restituito lo storico CSV'
                ));
            }
            const csv = await response.text();
            const downloadedMatches = parseMatches(csv);
            const sourceUrl = response.headers.get('X-Tennis-Source-Url');
            if (!sourceUrl) throw new Error('La risposta non identifica la fonte dello storico.');

            historyByTour.set(tour, downloadedMatches);
            if (tour !== root.document.getElementById('tennisTour').value) return;
            activeTour = tour;
            historyMatches = downloadedMatches;
            csvMatches = [];
            importedMatches = mergeMatchSources([
                historyMatches,
                scrapedTournamentMatches,
            ]);
            const playerCount = renderPlayers(importedMatches);
            const matchStatsCount = historyMatches.filter(match =>
                match.winner.servicePoints !== null && match.winner.servicePointsWon !== null &&
                match.loser.servicePoints !== null && match.loser.servicePointsWon !== null
            ).length;
            const sourceMatchCount = response.headers.get('X-Tennis-Match-Count');
            const invalidStats = Number(
                response.headers.get('X-Tennis-Invalid-Stat-Matches') || 0
            );
            setDataStatus(
                `${tour.toUpperCase()} ${startYear}–${currentYear}: ` +
                `${downloadedMatches.length.toLocaleString('it-IT')} match, ` +
                `${playerCount.toLocaleString('it-IT')} giocatori, ` +
                `${matchStatsCount.toLocaleString('it-IT')} con statistiche servizio complete.` +
                (invalidStats
                    ? ` Statistiche incoerenti escluse per ${invalidStats.toLocaleString('it-IT')} match.`
                    : '')
            );
            setHistoryStatus(
                `Storico ${tour.toUpperCase()} caricato (${startYear}–${currentYear}). ` +
                `La cache locale aggiorna i CSV ogni 24 ore. Fonte: ${sourceUrl}.`
            );
            root.document.getElementById('tennisResults').hidden = true;
            renderCalendar(Array.from(calendarMatchesById.values()));
        })();
        historyRequests.set(tour, request);
        try {
            await request;
        } catch (error) {
            if (tour !== root.document.getElementById('tennisTour').value) return;
            setHistoryStatus(
                `Storico non disponibile: ${apiFailureMessage(error)}`,
                true
            );
        } finally {
            historyRequests.delete(tour);
        }
    }

    async function loadCalendar(tour) {
        setCalendarStatus(`Carico il calendario ${tour.toUpperCase()} dei prossimi 14 giorni…`);
        root.document.getElementById('tennisCalendarMatches').textContent =
            'Caricamento delle partite in programma…';
        const params = new URLSearchParams({ tour, days: '14' });
        try {
            const response = await fetch(`/api/tennis-calendar?${params.toString()}`, {
                cache: 'no-store',
            });
            const payload = await readApiJson(
                response,
                'Impossibile caricare il calendario'
            );
            if (!Array.isArray(payload.matches)) {
                throw new Error('Il calendario ricevuto non contiene partite valide.');
            }
            if (tour !== root.document.getElementById('tennisTour').value) return;
            activeTour = tour;
            calendarMatchesById.clear();
            payload.matches.forEach(match => calendarMatchesById.set(match.id, match));
            renderCalendar(payload.matches);
            setCalendarStatus(
                `${payload.matches.length} match ${tour.toUpperCase()} con giocatori confermati ` +
                `nei prossimi 14 giorni · fonte ESPN · aggiornato ${new Date(payload.updated_at).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}.`
            );
        } catch (error) {
            if (tour !== root.document.getElementById('tennisTour').value) return;
            setCalendarStatus(
                `Calendario non disponibile: ${apiFailureMessage(error)}`,
                true
            );
        }
    }

    async function loadTennisDashboard(tour, force) {
        await Promise.all([loadHistory(tour, force), loadCalendar(tour)]);
    }

    function mergeMatchSources(groups) {
        const uniqueMatches = new Map();
        groups.flat().forEach(match => {
            const key = [
                match.date ? match.date.toISOString().slice(0, 10) : '',
                match.surface,
                match.winnerKey,
                match.loserKey,
                match.score,
            ].join('|');
            if (!uniqueMatches.has(key)) uniqueMatches.set(key, match);
        });
        return Array.from(uniqueMatches.values());
    }

    function scrapedMatchToModel(match) {
        const scoreTieBreaks = (match.score.match(/\d+\s*-\s*\d+(?:\(\d+\))?/g) || [])
            .map(set => set.match(/(\d+)\s*-\s*(\d+)/))
            .filter(set => set && ((Number(set[1]) === 7 && Number(set[2]) === 6) ||
                (Number(set[1]) === 6 && Number(set[2]) === 7)))
            .map(set => Number(set[1]) === 7);
        const emptyPlayerStats = (winner) => ({
            servicePoints: null,
            servicePointsWon: null,
            returnPoints: null,
            returnPointsTotal: null,
            breakPointsSaved: null,
            breakPointsFaced: null,
            tieBreaks: scoreTieBreaks.length,
            tieBreaksWon: scoreTieBreaks.filter(value => value === winner).length,
        });
        return {
            winnerName: match.winner,
            loserName: match.loser,
            winnerKey: normalizeName(match.winner),
            loserKey: normalizeName(match.loser),
            surface: match.surface,
            date: parseDate(match.date),
            minutes: null,
            tournament: match.tournament,
            score: match.score,
            source: 'Tennis Abstract',
            sourceUrl: match.sourceUrl,
            winner: emptyPlayerStats(true),
            loser: emptyPlayerStats(false),
        };
    }

    function formatPercent(value) {
        return value === null || value === undefined
            ? '—'
            : `${(value * 100).toLocaleString('it-IT', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
    }

    function formatFairOdds(probability) {
        if (!Number.isFinite(probability) || probability <= 0) return '—';
        return (1 / probability).toLocaleString('it-IT', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function renderPlayers(matches) {
        const names = new Map();
        matches.forEach(match => {
            names.set(match.winnerKey, match.winnerName);
            names.set(match.loserKey, match.loserName);
        });
        const datalist = root.document.getElementById('tennisPlayers');
        datalist.replaceChildren(...Array.from(names.values())
            .sort((a, b) => a.localeCompare(b, 'it'))
            .map(name => {
                const option = root.document.createElement('option');
                option.value = name;
                return option;
            }));
        return names.size;
    }

    function renderPlayerMetrics(player) {
        const stat = (label, value) =>
            `<div class="tennis-stat"><span>${label}</span><strong>${value}</strong></div>`;
        return `
            <article class="tennis-player-card">
                <h4>${escapeHtml(player.name)}</h4>
                <div class="tennis-stat-list">
                    ${stat('Vittorie / match', `${player.wins} / ${player.matches}`)}
                    ${stat('Win rate superficie', formatPercent(player.winRate))}
                    ${stat('Hold stimato', formatPercent(player.holdRate))}
                    ${stat('Break stimato', formatPercent(player.breakRate))}
                    ${stat('BP salvati', player.breakPointSaveRate === null
                        ? 'n.d.' : `${formatPercent(player.breakPointSaveRate)} (${player.breakPointsSaved}/${player.breakPointsFaced})`)}
                    ${stat('BP convertiti', player.breakPointConversionRate === null
                        ? 'n.d.' : `${formatPercent(player.breakPointConversionRate)} (${player.breakPointsConverted}/${player.breakPointsConversionChances})`)}
                    ${stat('Clutch factor', formatPercent(player.clutchFactor))}
                    ${stat('Tie-break vinti', player.tieBreakWinRate === null
                        ? 'n.d.' : `${formatPercent(player.tieBreakWinRate)} (${player.tieBreaksWon}/${player.tieBreaks})`)}
                    ${stat('Punti servizio', player.servicePoints.toLocaleString('it-IT'))}
                    ${stat('Punti risposta', player.returnPointsTotal.toLocaleString('it-IT'))}
                </div>
            </article>
        `;
    }

    function renderResult(result, matchCount) {
        const prediction = result.prediction;
        const bestOf = result.bestOf;
        const setsToWin = Math.ceil(bestOf / 2);
        const scoreLabel = (player, score) => {
            const probability = prediction.setScores[score] || 0;
            return `${escapeHtml(player)} ${score}: ${formatPercent(probability)}`;
        };
        const outcomeLabels = Array.from({ length: setsToWin }, (_, index) =>
            scoreLabel(result.playerA.name, `${setsToWin}-${index}`)
        ).concat(Array.from({ length: setsToWin }, (_, index) =>
            scoreLabel(result.playerB.name, `${index}-${setsToWin}`)
        )).join(' · ');
        const selectedSurface = root.document.getElementById('tennisSurface')
            .selectedOptions[0].textContent;
        const smallSample = result.playerA.matches < 10 || result.playerB.matches < 10;
        const probabilityA = prediction.winA * 100;
        const probabilityB = prediction.winB * 100;
        const scrapedMatchCount = result.surface.matches.filter(match =>
            match.source === 'Tennis Abstract'
        ).length;
        const h2hSummary = result.h2h.matches
            ? `${result.h2h.winsA}–${result.h2h.winsB} in ${result.h2h.matches} match sulla stessa superficie`
            : 'Nessun precedente trovato sulla stessa superficie';
        const currentTournament = result.tournamentMinutesA + result.tournamentMinutesB;
        const fatigue = result.fatigueShift > 0
            ? `${escapeHtml(result.playerA.name)} con minore carico recente`
            : result.fatigueShift < 0
                ? `${escapeHtml(result.playerB.name)} con minore carico recente`
                : 'Carico recente simile';

        root.document.getElementById('tennisPrediction').innerHTML = `
            <div class="tennis-results-heading">
                <div>
                    <h2>${escapeHtml(result.playerA.name)} <span class="versus">VS</span> ${escapeHtml(result.playerB.name)}</h2>
                    <p>${escapeHtml(selectedSurface)} · ${bestOf === 3 ? 'al meglio dei 3' : 'al meglio dei 5'} · ${matchCount} match storici disponibili</p>
                </div>
                <span class="date-tag">${smallSample ? 'CAMPIONE RIDOTTO' : 'QUOTA EQUA DEL MODELLO'}</span>
            </div>
            <div class="tennis-winbar" role="img" aria-label="Probabilità vittoria ${escapeHtml(result.playerA.name)} ${formatPercent(prediction.winA)} e ${escapeHtml(result.playerB.name)} ${formatPercent(prediction.winB)}">
                <div class="tennis-winbar-a" style="width:${probabilityA}%"></div>
                <div class="tennis-winbar-b" style="width:${probabilityB}%"></div>
            </div>
            <div class="tennis-win-labels">
                <strong>${escapeHtml(result.playerA.name)} ${formatPercent(prediction.winA)}</strong>
                <strong>${escapeHtml(result.playerB.name)} ${formatPercent(prediction.winB)}</strong>
            </div>
            <div class="tennis-output-grid">
                <div class="tennis-output-card">
                    <div class="tennis-output-label">Quote eque stimate · senza bookmaker</div>
                    <div class="tennis-output-value">${formatFairOdds(prediction.winA)} · ${formatFairOdds(prediction.winB)}</div>
                    <div class="tennis-output-detail">${escapeHtml(result.playerA.name)} · ${escapeHtml(result.playerB.name)} · probabilità reciproche, senza margine</div>
                </div>
                <div class="tennis-output-card">
                    <div class="tennis-output-label">Esiti set</div>
                    <div class="tennis-output-detail">${outcomeLabels}</div>
                </div>
                <div class="tennis-output-card">
                    <div class="tennis-output-label">Game totali attesi</div>
                    <div class="tennis-output-value">${prediction.expectedGames.toLocaleString('it-IT', { maximumFractionDigits: 1 })}</div>
                    <div class="tennis-output-detail">linea ${result.line} · Over ${formatPercent(prediction.over)} · Under ${formatPercent(prediction.under)}${prediction.push ? ` · Push ${formatPercent(prediction.push)}` : ''}</div>
                </div>
                <div class="tennis-output-card">
                    <div class="tennis-output-label">Almeno un tie-break</div>
                    <div class="tennis-output-value">${formatPercent(prediction.atLeastOneTieBreak)}</div>
                    <div class="tennis-output-detail">stimato sui set al 6–6</div>
                </div>
            </div>
        `;

        root.document.getElementById('tennisPlayerMetrics').innerHTML =
            renderPlayerMetrics(result.playerA) + renderPlayerMetrics(result.playerB);
        root.document.getElementById('tennisContextMetrics').innerHTML = `
            <div class="tennis-output-card">
                <div class="tennis-output-label">Ultimi 21 giorni · tutti i campi</div>
                <div class="tennis-output-detail"><strong>${escapeHtml(result.playerA.name)}:</strong> ${result.recentA.matches} match · ${result.recentA.minutes} min registrati</div>
                <div class="tennis-output-detail"><strong>${escapeHtml(result.playerB.name)}:</strong> ${result.recentB.matches} match · ${result.recentB.minutes} min registrati</div>
            </div>
            <div class="tennis-output-card">
                <div class="tennis-output-label">Minuti nel torneo attuale · manuali</div>
                <div class="tennis-output-detail"><strong>${escapeHtml(result.playerA.name)}:</strong> ${result.tournamentMinutesA} min</div>
                <div class="tennis-output-detail"><strong>${escapeHtml(result.playerB.name)}:</strong> ${result.tournamentMinutesB} min</div>
            </div>
            <div class="tennis-output-card">
                <div class="tennis-output-label">H2H · superficie selezionata${scrapedMatchCount ? ' · incluso scraping' : ''}</div>
                <div class="tennis-output-value">${result.h2h.matches ? `${result.h2h.winsA}–${result.h2h.winsB}` : '—'}</div>
                <div class="tennis-output-detail">${h2hSummary}</div>
            </div>
        `;
        root.document.getElementById('tennisModelNote').textContent =
            `${smallSample ? 'Campione ridotto: servono almeno 10 match sulla superficie per ciascun giocatore per una stima più stabile. ' : ''}` +
            `Hold/break stimati dai punti di servizio e risposta, con regressione verso la media della superficie (prior di ${SHRINKAGE_POINTS} punti). Carico recente: ${fatigue}. La correzione di fatica è limitata a ±2 punti percentuali ed è euristica, non calibrata su dati live. Punti A al servizio stimati: ${formatPercent(result.pointA)}; punti B al servizio: ${formatPercent(result.pointB)}. Break point e tie-break sono riportati solo se presenti nel CSV. Il modello assume un tie-break standard a 6–6 in ogni set, inclusi eventuali set decisivi. I dati dei tornei riportano talvolta solo la data d'inizio: il conteggio dei 21 giorni può quindi essere approssimativo.`;
        if (scrapedMatchCount) {
            root.document.getElementById('tennisModelNote').textContent +=
                ` Inclusi ${scrapedMatchCount} risultati recenti importati da Tennis Abstract: la data è quella d'inizio torneo inserita manualmente, non la data effettiva del match; i record scraping contribuiscono a forma, vittorie/sconfitte, H2H e tie-break, non alle metriche servizio/break.`;
        }
        root.document.getElementById('tennisResults').hidden = false;
    }

    function switchView(view) {
        if (typeof root.switchSportView === 'function') {
            root.switchSportView(view);
            return;
        }
        const isTennis = view === 'tennis';
        const isFormula1 = view === 'formula1';
        const isFootball = !isTennis && !isFormula1;
        root.document.getElementById('footballView').hidden = !isFootball;
        root.document.getElementById('tennisView').hidden = !isTennis;
        root.document.getElementById('formula1View').hidden = !isFormula1;
        root.document.getElementById('footballTab').setAttribute('aria-selected', String(isFootball));
        root.document.getElementById('tennisTab').setAttribute('aria-selected', String(isTennis));
        root.document.getElementById('formula1Tab').setAttribute('aria-selected', String(isFormula1));
        root.document.querySelector('.header-actions').hidden = !isFootball;
        root.document.querySelector('.brand-name').innerHTML = isTennis
            ? 'TENNIS <span>/</span> ANALYTICS'
            : isFormula1
                ? 'FORMULA 1 <span>/</span> ANALYTICS'
                : 'CALCIO <span>/</span> ANALYTICS';
        root.document.querySelector('.brand-caption').textContent = isTennis
            ? 'Match intelligence, senza quote.'
            : isFormula1
                ? 'Dati di gara e feature pre-gara.'
                : 'Match intelligence, senza rumore.';
        root.document.querySelectorAll('.bottom-nav a').forEach(link => {
            link.hidden = !isFootball;
        });
        root.document.querySelector('.bottom-nav button[onclick*="toggleOddsModal"]')?.toggleAttribute('hidden', !isFootball);
        const mobileTab = root.document.getElementById('tennisMobileTab');
        if (mobileTab) mobileTab.hidden = !root.matchMedia('(max-width: 760px)').matches;
        const f1Mobile = root.document.getElementById('formula1MobileTab');
        if (f1Mobile) f1Mobile.hidden = !root.matchMedia('(max-width: 760px)').matches;
        if (isTennis) root.document.getElementById('tennisTab').scrollIntoView({ block: 'nearest' });
        if (isFormula1) root.document.getElementById('formula1Tab').scrollIntoView({ block: 'nearest' });
    }

    function initialize() {
        const today = new Date();
        const localDate = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
            .toISOString().slice(0, 10);
        root.document.getElementById('tennisMatchDate').value = localDate;
        root.document.getElementById('tennisTournamentDate').value = localDate;

        root.document.getElementById('footballTab').addEventListener('click', () => switchView('football'));
        const tourSelector = root.document.getElementById('tennisTour');
        root.document.getElementById('tennisTab').addEventListener('click', () => {
            switchView('tennis');
            loadTennisDashboard(tourSelector.value, false);
        });
        root.document.getElementById('formula1Tab').addEventListener('click', () => switchView('formula1'));
        root.document.getElementById('formula1MobileTab').addEventListener('click', () => switchView('formula1'));
        tourSelector.addEventListener('change', () => {
            activeTour = tourSelector.value;
            historyMatches = historyByTour.get(activeTour) || [];
            importedMatches = mergeMatchSources([
                historyMatches,
                csvMatches,
                scrapedTournamentMatches,
            ]);
            renderPlayers(importedMatches);
            loadTennisDashboard(activeTour, false);
        });
        root.document.getElementById('tennisRefresh').addEventListener('click', () => {
            loadTennisDashboard(tourSelector.value, true);
        });
        root.document.getElementById('tennisMobileTab').addEventListener('click', () => {
            switchView('tennis');
            loadTennisDashboard(tourSelector.value, false);
        });
        root.document.getElementById('tennisCalendarMatches').addEventListener('click', event => {
            const button = event.target.closest('[data-tennis-match]');
            if (!button) return;
            const match = calendarMatchesById.get(button.dataset.tennisMatch);
            if (!match) {
                setDataStatus('Il match selezionato non è più disponibile nel calendario. Aggiorna i dati.', true);
                return;
            }
            const form = root.document.getElementById('tennisMatchForm');
            form.elements.playerA.value = match.playerA;
            form.elements.playerB.value = match.playerB;
            form.elements.surface.value = match.surface || '';
            form.elements.bestOf.value = String(match.bestOf);
            const matchDate = new Date(match.date);
            form.elements.matchDate.value = new Date(
                matchDate.getTime() - matchDate.getTimezoneOffset() * 60000
            ).toISOString().slice(0, 10);
            form.elements.minutesA.value = '0';
            form.elements.minutesB.value = '0';
            form.scrollIntoView({ behavior: 'smooth', block: 'start' });
            if (!match.surface) {
                setDataStatus(
                    `La superficie di ${match.tournament} non è verificabile dallo storico: selezionala prima del pronostico.`,
                    true
                );
                root.document.getElementById('tennisSurface').focus();
                return;
            }
            form.requestSubmit();
        });
        root.document.getElementById('tennisCsvFile').addEventListener('change', async event => {
            const files = Array.from(event.target.files || []);
            if (!files.length) return;
            if (files.reduce((total, file) => total + file.size, 0) > 100 * 1024 * 1024) {
                setDataStatus('I CSV selezionati superano il limite complessivo di 100 MB.', true);
                event.target.value = '';
                return;
            }
            setDataStatus(`Lettura di ${files.length} CSV in corso…`);
            try {
                const matchGroups = await Promise.all(files.map(async file =>
                    parseMatches(await file.text())
                ));
                csvMatches = matchGroups.flat();
                importedMatches = mergeMatchSources([
                    historyMatches,
                    csvMatches,
                    scrapedTournamentMatches,
                ]);
                const playerCount = renderPlayers(importedMatches);
                const matchStatsCount = importedMatches.filter(match =>
                    match.winner.servicePoints !== null && match.winner.servicePointsWon !== null &&
                    match.loser.servicePoints !== null && match.loser.servicePointsWon !== null
                ).length;
                const surfaces = Array.from(new Set(importedMatches.map(match => match.surface).filter(Boolean)));
                const surfaceLabels = surfaces.map(surface => ({
                    clay: 'terra',
                    hard_outdoor: 'cemento outdoor',
                    hard_indoor: 'cemento indoor',
                    hard_unspecified: 'cemento non specificato',
                    grass: 'erba',
                })[surface]).join(', ') || 'nessuna superficie riconosciuta';
                setDataStatus(
                    `${files.length} CSV, ${csvMatches.length} match, ${scrapedTournamentMatches.length} risultati scraping, ` +
                    `${importedMatches.length} match unici, ${playerCount} giocatori, ` +
                    `${matchStatsCount} match con statistiche di servizio complete per entrambi. ` +
                    `Superfici: ${surfaceLabels}.`
                );
                root.document.getElementById('tennisResults').hidden = true;
            } catch (error) {
                setDataStatus(`Importazione non riuscita: ${error.message}`, true);
                event.target.value = '';
            }
        });

        root.document.getElementById('tennisScrapeForm').addEventListener('submit', async event => {
            event.preventDefault();
            const form = event.currentTarget;
            const button = form.querySelector('button[type="submit"]');
            const params = new URLSearchParams({
                url: form.elements.url.value.trim(),
                surface: form.elements.surface.value,
                date: form.elements.date.value,
            });
            button.disabled = true;
            setScrapeStatus('Recupero dei risultati completati da Tennis Abstract…');
            try {
                const response = await fetch(`/api/tennis-results?${params.toString()}`, {
                    cache: 'no-store',
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.error || 'Impossibile acquisire i risultati del torneo.');
                }
                const scraped = payload.matches.map(scrapedMatchToModel);
                scrapedTournamentMatches = mergeMatchSources([
                    scrapedTournamentMatches.filter(match => match.sourceUrl !== payload.source_url),
                    scraped.map(match => ({ ...match, sourceUrl: payload.source_url })),
                ]);
                importedMatches = mergeMatchSources([
                    csvMatches,
                    historyMatches,
                    scrapedTournamentMatches,
                ]);
                const playerCount = renderPlayers(importedMatches);
                setScrapeStatus(
                    `${payload.tournament}: importati ${scraped.length} risultati · ` +
                    `${payload.surface} · data torneo ${payload.tournament_date}. ` +
                    `La pagina non fornisce statistiche servizio/break. ${playerCount} giocatori disponibili.`
                );
            } catch (error) {
                setScrapeStatus(
                    `Scraping non riuscito: ${error.message}. ` +
                    'Lo scraper richiede il server locale e una pagina torneo valida.',
                    true
                );
            } finally {
                button.disabled = false;
            }
        });

        root.document.getElementById('tennisMatchForm').addEventListener('submit', event => {
            event.preventDefault();
            if (!importedMatches.length) {
                setDataStatus('Prima importa un CSV storico.', true);
                return;
            }
            const form = event.currentTarget;
            const options = {
                playerA: form.elements.playerA.value.trim(),
                playerB: form.elements.playerB.value.trim(),
                surface: form.elements.surface.value,
                bestOf: form.elements.bestOf.value,
                matchDate: form.elements.matchDate.value,
                gameLine: form.elements.gameLine.value,
                tournamentMinutesA: form.elements.minutesA.value,
                tournamentMinutesB: form.elements.minutesB.value,
            };
            if (normalizeName(options.playerA) === normalizeName(options.playerB)) {
                setDataStatus('Seleziona due giocatori diversi.', true);
                return;
            }
            if (!options.surface) {
                setDataStatus('Conferma la superficie del match prima di calcolare il pronostico.', true);
                root.document.getElementById('tennisSurface').focus();
                return;
            }
            try {
                const result = analyzeMatch(importedMatches, options);
                renderResult(result, result.surface.matches.length);
                setDataStatus(
                    `Analisi completata su ${result.surface.matches.length} match della superficie selezionata.`
                );
            } catch (error) {
                root.document.getElementById('tennisResults').hidden = true;
                setDataStatus(`Pronostico non disponibile: ${error.message}`, true);
            }
        });
        switchView('football');
    }

    if (root.document.readyState === 'loading') {
        root.document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
}(typeof window === 'undefined' ? globalThis : window));
