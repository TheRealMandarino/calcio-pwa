'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Tennis = require('../docs/tennis/tennis.js');

const csv = [
    'tourney_date,tourney_name,surface,winner_name,loser_name,score,best_of,minutes,w_svpt,w_1stWon,w_2ndWon,w_bpSaved,w_bpFaced,l_svpt,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced',
    '20251001,"Rome, Open",Clay,Alpha,Beta,7-6(4) 6-4,3,120,100,60,20,4,6,100,54,18,3,5',
    '20251003,Rome Open,Clay,Beta,Alpha,6-4 7-6(5),3,110,95,58,18,3,5,96,55,17,4,6',
    '20251005,Rome Open,Clay,Alpha,Beta,6-4 6-3,3,90,88,52,19,2,4,87,48,16,2,4',
    '20251006,Indoor Open,Hard,Alpha,Beta,6-2 6-2,3,70,70,46,15,1,2,65,35,10,0,1',
].join('\n');

test('CSV parser supports quoted commas and normalizes headers', () => {
    const rows = Tennis.parseCsv(csv);
    assert.equal(rows[0].tourneyname, 'Rome, Open');
    assert.equal(rows[0].winnername, 'Alpha');
    assert.equal(Tennis.normalizeSurface('Hard'), 'hard_unspecified');
    assert.equal(Tennis.normalizeSurface('Hard Indoor'), 'hard_indoor');
    assert.equal(Tennis.normalizeSurface('Hard Outdoor'), 'hard_outdoor');
});

test('surface aggregates exclude other surfaces and calculate advanced metrics', () => {
    const matches = Tennis.parseMatches(csv);
    const clay = Tennis.summarizeSurface(matches, 'clay');
    const alpha = clay.players.get('alpha');
    const beta = clay.players.get('beta');

    assert.equal(clay.matches.length, 3);
    assert.equal(alpha.matches, 3);
    assert.equal(alpha.wins, 2);
    assert.equal(beta.wins, 1);
    assert.equal(alpha.tieBreaks, 1);
    assert.equal(alpha.tieBreaksWon, 1);
    assert.ok(alpha.holdRate > 0 && alpha.holdRate < 1);
    assert.ok(alpha.breakRate > 0 && alpha.breakRate < 1);
    assert.ok(alpha.breakPointSaveRate > 0 && alpha.breakPointSaveRate <= 1);
    assert.ok(alpha.breakPointConversionRate >= 0 && alpha.breakPointConversionRate <= 1);
    assert.equal(Tennis.summarizeSurface(matches, 'hard_indoor').matches.length, 0);
    assert.equal(Tennis.summarizeSurface(matches, 'hard_unspecified').matches.length, 1);
});

test('match probabilities include scores, game totals, tie-breaks and fatigue', () => {
    const matches = Tennis.parseMatches(csv);
    const result = Tennis.analyzeMatch(matches, {
        playerA: 'Alpha',
        playerB: 'Beta',
        surface: 'clay',
        matchDate: '2025-10-10',
        bestOf: '3',
        gameLine: '21.5',
        tournamentMinutesA: '120',
        tournamentMinutesB: '0',
    });

    assert.equal(result.h2h.matches, 3);
    assert.equal(result.recentA.matches, 4);
    assert.ok(result.fatigueShift < 0);
    assert.ok(Math.abs(result.prediction.totalProbability - 1) < 1e-10);
    assert.ok(Math.abs(result.prediction.winA + result.prediction.winB - 1) < 1e-10);
    assert.ok(Math.abs(result.prediction.over + result.prediction.under +
        result.prediction.push - 1) < 1e-10);
    assert.ok(result.prediction.expectedGames > 12);
    assert.ok(result.prediction.expectedGames < 60);
    assert.ok(result.prediction.atLeastOneTieBreak >= 0);
    assert.ok(result.prediction.atLeastOneTieBreak <= 1);
    assert.ok(result.prediction.setScores['2-0'] > 0);
    assert.ok(result.prediction.setScores['0-2'] > 0);
});

test('best-of-five scores are generated and surface stats cannot silently fall back', () => {
    const matches = Tennis.parseMatches(csv);
    const result = Tennis.analyzeMatch(matches, {
        playerA: 'Alpha',
        playerB: 'Beta',
        surface: 'clay',
        matchDate: '2025-10-10',
        bestOf: '5',
        gameLine: '21.5',
        tournamentMinutesA: '0',
        tournamentMinutesB: '0',
    });

    assert.ok(result.prediction.setScores['3-0'] > 0);
    assert.ok(result.prediction.setScores['0-3'] > 0);
    assert.throws(() => Tennis.analyzeMatch(matches, {
        playerA: 'Alpha',
        playerB: 'Beta',
        surface: 'hard_indoor',
        matchDate: '2025-10-10',
        bestOf: '3',
        gameLine: '21.5',
    }), /Alpha: storico disponibile su cemento non specificato \(1\)/);
});

test('unknown players are distinguished from players with history on another surface', () => {
    const matches = Tennis.parseMatches(csv);
    assert.throws(() => Tennis.analyzeMatch(matches, {
        playerA: 'Alpha',
        playerB: 'Unknown Player',
        surface: 'hard_indoor',
        matchDate: '2025-10-10',
        bestOf: '3',
        gameLine: '21.5',
    }), /Unknown Player: nessun match nello storico caricato o nome non riconosciuto/);
});

test('invalid headers and inconsistent serve totals are rejected', () => {
    assert.throws(() => Tennis.parseMatches('surface,winner_name\nClay,Alpha'), /Colonne obbligatorie/);
    const invalid = csv.replace(',100,60,20,4,6,', ',100,90,20,4,6,');
    assert.throws(() => Tennis.parseMatches(invalid), /superiori ai punti giocati/);
});
