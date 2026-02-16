import streamlit as st
import pybaseball as pb
import pandas as pd
import pulp
import json
from datetime import date

st.title("Fantasy Baseball Lineup Optimizer")

# User selects season year
default_year = date.today().year - 1  # Use last complete season by default
year = st.number_input("Select season year for stats", min_value=1871, max_value=date.today().year, value=default_year)

# Fetch stats (cached by pybaseball)
with st.spinner("Fetching batting and pitching stats from FanGraphs..."):
    batting_df = pb.batting_stats(year, qual=0)
    pitching_df = pb.pitching_stats(year, qual=0)
st.success("Stats loaded!")

# Input scoring systems as JSON
st.header("Enter Scoring Systems")

batter_scoring_str = st.text_area(
    "Batter scoring (JSON dict) – uses FanGraphs names (SO = strikeouts, GDP = GIDP)",
    value='{"R": 1, "1B": 1, "2B": 2, "3B": 3, "HR": 4, "RBI": 1, "SB": 2, "CS": -1, "BB": 1, "IBB": 1, "HBP": 1, "SO": -1, "GDP": -1}'
)

pitcher_scoring_str = st.text_area(
    "Pitcher scoring (JSON dict) – uses FanGraphs names (SO = strikeouts, BS = blown saves)",
    value='{"W": 10, "L": -5, "CG": 10, "SHO": 5, "SV": 10, "IP": 3, "H": -1, "ER": -1, "BB": -1, "IBB": -1, "HBP": -1.3, "SO": 1, "WP": -1, "HLD": 7, "BS": -5}'
)

try:
    batter_scoring = json.loads(batter_scoring_str)
    pitcher_scoring = json.loads(pitcher_scoring_str)
except json.JSONDecodeError:
    st.error("Invalid JSON for scoring. Please check formatting and try again.")
    st.stop()

# ────────────────────────────────────────────────
# Searchable player selector
# ────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def get_all_players(year):
    try:
        batting = pb.batting_stats(year, qual=0)
        pitching = pb.pitching_stats(year, qual=0)
        
        batters = batting[['Name', 'Team']].copy()
        batters['Type'] = 'batter'
        
        pitchers = pitching[['Name', 'Team']].copy()
        pitchers['Type'] = 'pitcher'
        
        all_players = pd.concat([batters, pitchers], ignore_index=True)
        all_players['Display'] = all_players['Name'] + " (" + all_players['Type'].str[0].str.upper() + ", " + all_players['Team'].fillna('FA') + ")"
        all_players = all_players.sort_values('Name').drop_duplicates('Name')
        return all_players
    except Exception as e:
        st.error(f"Error loading players: {str(e)}")
        return pd.DataFrame()

players_df = get_all_players(year)

st.header("Build Your Roster")
st.info("Type to search (e.g. 'Judge', 'Skenes'). Select your full roster (starters, bench, IL).")

roster = []  # Always define it here

if players_df.empty:
    st.warning("No players loaded. Try a different year or check connection.")
else:
    selected_displays = st.multiselect(
        "Search & add players",
        options=players_df['Display'].tolist(),
        default=[],
        placeholder="Start typing a name...",
        max_selections=40
    )

    for disp in selected_displays:
        row = players_df[players_df['Display'] == disp].iloc[0]
        name = row['Name']
        typ = row['Type']
        
        # Basic position fallback (since no Pos column)
        positions = ['UTIL'] if typ == 'batter' else ['P']
        if typ == 'pitcher':
            if 'SP' in name.upper(): positions = ['SP', 'P']  # rough guess
            elif 'RP' in name.upper(): positions = ['RP', 'P']
        
        roster.append({"name": name, "type": typ, "positions": positions})

    if selected_displays:
        st.success(f"Added {len(roster)} players. Ready!")
    else:
        st.info("Select players to build roster.")

# ────────────────────────────────────────────────
# Safe memory optimization
# ────────────────────────────────────────────────
if roster:
    player_names = [p['name'] for p in roster]
    
    batter_needed = ['Name'] + [k for k in batter_scoring if k in batting_df.columns]
    pitcher_needed = ['Name'] + [k for k in pitcher_scoring if k in pitching_df.columns]
    
    missing_b = [k for k in batter_scoring if k not in batting_df.columns]
    missing_p = [k for k in pitcher_scoring if k not in pitching_df.columns]
    if missing_b:
        st.warning(f"Batter stats ignored (missing): {', '.join(missing_b)}")
    if missing_p:
        st.warning(f"Pitcher stats ignored (missing): {', '.join(missing_p)}")
    
    batting_df = batting_df[batting_df['Name'].isin(player_names)][batter_needed]
    pitching_df = pitching_df[pitching_df['Name'].isin(player_names)][pitcher_needed]
    
    st.info(f"Filtered to your {len(player_names)} players (memory saved).")

# ────────────────────────────────────────────────
# Calculate points
# ────────────────────────────────────────────────
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
        player['points'] = sum(row.get(stat, 0) * coeff for stat, coeff in batter_scoring.items())

for player in pitchers:
    match = pitching_df[pitching_df['Name'] == player['name']]
    if match.empty:
        unmatched.append(player['name'])
        player['points'] = 0
    else:
        row = match.iloc[0]
        player['points'] = sum(row.get(stat, 0) * coeff for stat, coeff in pitcher_scoring.items())

if unmatched:
    st.warning(f"No stats found for: {', '.join(unmatched)}. Points = 0.")

# Hardcoded slots & eligibility
hitter_slots = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'CI': 1, 'MI': 1, 'OF': 5, 'UTIL': 1}
pitcher_slots = {'SP': 2, 'RP': 2, 'P': 5}

hitter_eligible = {
    'C': ['C'], '1B': ['1B'], '2B': ['2B'], '3B': ['3B'], 'SS': ['SS'],
    'CI': ['1B', '3B'], 'MI': ['2B', 'SS'],
    'OF': ['OF'], 'UTIL': ['C', '1B', '2B', '3B', 'SS', 'OF']
}

pitcher_eligible = {'SP': ['SP'], 'RP': ['RP'], 'P': ['SP', 'RP']}

# Optimize function
def optimize_lineup(players, slots, eligible):
    if not players:
        return {}, 0.0
    prob = pulp.LpProblem("Lineup", pulp.LpMaximize)
    x = {}
    for i, p in enumerate(players):
        for s in slots:
            if set(p['positions']) & set(eligible[s]):
                x[(i, s)] = pulp.LpVariable(f"x_{i}_{s}", cat='Binary')
    prob += pulp.lpSum(x[(i, s)] * players[i]['points'] for i, s in x)
    for s in slots:
        prob += pulp.lpSum(x.get((i, s), 0) for i in range(len(players))) == slots[s]
    for i in range(len(players)):
        prob += pulp.lpSum(x.get((i, s), 0) for s in slots) <= 1
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    lineup = {s: [] for s in slots}
    for var in x:
        if x[var].value() == 1:
            i, s = var
            lineup[s].append(f"{players[i]['name']} ({players[i]['points']:.2f})")
    return lineup, pulp.value(prob.objective) or 0.0

# Button
if st.button("Suggest Lineup"):
    if not roster:
        st.error("Select some players first.")
    else:
        hitter_lineup, hitter_pts = optimize_lineup(hitters, hitter_slots, hitter_eligible)
        pitcher_lineup, pitcher_pts = optimize_lineup(pitchers, pitcher_slots, pitcher_eligible)

        st.header("Suggested Hitters")
        for slot, lst in hitter_lineup.items():
            st.write(f"**{slot}**: {', '.join(lst) or 'None'}")
        st.write(f"Hitter Points: **{hitter_pts:.2f}**")

        st.header("Suggested Pitchers")
        for slot, lst in pitcher_lineup.items():
            st.write(f"**{slot}**: {', '.join(lst) or 'None'}")
        st.write(f"Pitcher Points: **{pitcher_pts:.2f}**")

        st.write(f"**Grand Total: {hitter_pts + pitcher_pts:.2f}**")
