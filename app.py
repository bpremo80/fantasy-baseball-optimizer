import streamlit as st
import pybaseball as pb
import pandas as pd
import pulp
import json
from datetime import date

st.title("Fantasy Baseball Lineup Optimizer")

# User selects season year
default_year = date.today().year - 1  # Use last complete season by default
year = st.number_input("Select season year for stats", min_value=1871, max_value= date.today().year, value=default_year)

# Fetch stats (cached by pybaseball)
with st.spinner("Fetching batting and pitching stats from FanGraphs..."):
    batting_df = pb.batting_stats(year, qual=0)  # qual=0 to include all players
    pitching_df = pb.pitching_stats(year, qual=0)

st.success("Stats loaded!")

# Input scoring systems as JSON
st.header("Enter Scoring Systems")
batter_scoring_str = st.text_area("Batter scoring (JSON dict, e.g. {'HR': 4, 'R': 1, 'RBI': 1, 'SB': 2, 'H': 1})", value='{"HR": 4, "R": 1, "RBI": 1, "SB": 2, "H": 1}')
pitcher_scoring_str = st.text_area("Pitcher scoring (JSON dict, e.g. {'W': 5, 'SO': 1, 'SV': 5, 'IP': 3, 'ER': -1})", value='{"W": 5, "SO": 1, "SV": 5, "IP": 3, "ER": -1}')

try:
    batter_scoring = json.loads(batter_scoring_str)
    pitcher_scoring = json.loads(pitcher_scoring_str)
except json.JSONDecodeError:
    st.error("Invalid JSON for scoring. Please fix and try again.")
    st.stop()

# Input roster as JSON list
st.header("Enter Your Roster")
roster_str = st.text_area(
    "Roster (JSON list of dicts, e.g. [{'name': 'Aaron Judge', 'type': 'batter', 'positions': ['OF']}, {'name': 'Gerrit Cole', 'type': 'pitcher', 'positions': ['SP']}, ...])",
    height=200
)

try:
    roster = json.loads(roster_str)
except json.JSONDecodeError:
    st.error("Invalid JSON for roster. Please fix and try again.")
    st.stop()

# Hardcoded standard lineup slots and eligibility (you can customize if needed)
hitter_slots = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'CI': 1, 'MI': 1, 'OF': 5, 'UTIL': 1}
pitcher_slots = {'SP': 2, 'RP': 2, 'P': 5}

hitter_eligible = {
    'C': ['C'],
    '1B': ['1B'],
    '2B': ['2B'],
    '3B': ['3B'],
    'SS': ['SS'],
    'CI': ['1B', '3B'],
    'MI': ['2B', 'SS'],
    'OF': ['OF'],
    'UTIL': ['C', '1B', '2B', '3B', 'SS', 'OF']
}

pitcher_eligible = {
    'SP': ['SP'],
    'RP': ['RP'],
    'P': ['SP', 'RP']
}

# Calculate points for each player
hitters = [p for p in roster if p['type'] == 'batter']
pitchers = [p for p in roster if p['type'] == 'pitcher']

unmatched = []

for player in hitters:
    match = batting_df[batting_df['Name'] == player['name']]
    if match.empty:
        unmatched.append(player['name'])
        player['points'] = 0
    else:
        row = match.iloc[0]
        player['points'] = sum(row.get(stat, 0) * coeff for stat, coeff in batter_scoring.items() if stat in row)

for player in pitchers:
    match = pitching_df[pitching_df['Name'] == player['name']]
    if match.empty:
        unmatched.append(player['name'])
        player['points'] = 0
    else:
        row = match.iloc[0]
        player['points'] = sum(row.get(stat, 0) * coeff for stat, coeff in pitcher_scoring.items() if stat in row)

if unmatched:
    st.warning(f"Could not find stats for: {', '.join(unmatched)}. Their points set to 0.")

# Optimize lineup
def optimize_lineup(players, slots, eligible):
    if not players:
        return {}

    prob = pulp.LpProblem("Lineup_Optimizer", pulp.LpMaximize)
    x = {}
    for i, p in enumerate(players):
        for s in slots:
            if set(p['positions']) & set(eligible[s]):
                x[(i, s)] = pulp.LpVariable(f"x_{i}_{s}", cat='Binary')

    # Objective: maximize points
    prob += pulp.lpSum(x[(i, s)] * players[i]['points'] for i, s in x.keys())

    # Constraints: fill slots exactly
    for s in slots:
        prob += pulp.lpSum(x.get((i, s), 0) for i in range(len(players))) == slots[s]

    # Each player used at most once
    for i in range(len(players)):
        prob += pulp.lpSum(x.get((i, s), 0) for s in slots) <= 1

    # Solve
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    lineup = {s: [] for s in slots}
    for i, s in x:
        if x[(i, s)].value() == 1:
            lineup[s].append(f"{players[i]['name']} ({players[i]['points']:.2f} pts)")

    total_points = pulp.value(prob.objective)
    return lineup, total_points

if st.button("Suggest Lineup"):
    hitter_lineup, hitter_pts = optimize_lineup(hitters, hitter_slots, hitter_eligible)
    pitcher_lineup, pitcher_pts = optimize_lineup(pitchers, pitcher_slots, pitcher_eligible)

    st.header("Suggested Hitter Lineup")
    for slot, assigned in hitter_lineup.items():
        st.write(f"{slot}: {', '.join(assigned) or 'None'}")

    st.write(f"Total Hitter Points: {hitter_pts:.2f}")

    st.header("Suggested Pitcher Lineup")
    for slot, assigned in pitcher_lineup.items():
        st.write(f"{slot}: {', '.join(assigned) or 'None'}")

    st.write(f"Total Pitcher Points: {pitcher_pts:.2f}")
    st.write(f"Grand Total: {hitter_pts + pitcher_pts:.2f}")