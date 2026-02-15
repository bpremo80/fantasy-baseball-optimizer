import streamlit as st
import pybaseball as pb
import pandas as pd
import pulp
from datetime import date

st.title("Fantasy Baseball Lineup Optimizer")

# User selects season year
default_year = date.today().year - 1  # Use last complete season by default
year = st.number_input("Select season year for stats", min_value=1871, max_value=date.today().year, value=default_year)

# Fetch stats (cached by pybaseball)
with st.spinner("Fetching batting and pitching stats from FanGraphs..."):
    batting_df = pb.batting_stats(year, qual=0)  # qual=0 to include all players
    pitching_df = pb.pitching_stats(year, qual=0)
st.success("Stats loaded!")

# Input scoring systems as JSON
st.header("Enter Scoring Systems")
batter_scoring_str = st.text_area(
    "Batter scoring (JSON dict, e.g. {'HR': 4, 'R': 1, 'RBI': 1, 'SB': 2, 'H': 1})",
    value='{"R": 1, "1B": 1, "2B": 2, "3B": 3, "HR": 4, "RBI": 1, "SB": 2, "CS": -1, "BB": 1, "IBB": 1, "HBP": 1, "K": -1, "GIDP": -1, "E": -1}'
)
pitcher_scoring_str = st.text_area(
    "Pitcher scoring (JSON dict, e.g. {'W': 10, 'L': -5, ...})",
    value='{"W": 10, "L": -5, "CG": 10, "SHO": 5, "SV": 10, "IP": 3, "H": -1, "ER": -1, "BB": -1, "IBB": -1, "HBP": -1.3, "K": 1, "WP": -1, "HLD": 7, "BS": -5}'
)

try:
    batter_scoring = json.loads(batter_scoring_str)
    pitcher_scoring = json.loads(pitcher_scoring_str)
except json.JSONDecodeError:
    st.error("Invalid JSON for scoring. Please fix and try again.")
    st.stop()

# ────────────────────────────────────────────────
# NEW: Searchable player selector (replaces JSON roster input)
# ────────────────────────────────────────────────

@st.cache_data(ttl=3600)  # cache 1 hour
def get_all_players(year):
    try:
        batting = pb.batting_stats(year, qual=0)
        pitching = pb.pitching_stats(year, qual=0)
        
        batters = batting[['Name', 'Team', 'Pos']].copy()
        batters['Type'] = 'batter'
        batters = batters.rename(columns={'Pos': 'Primary_Pos'})
        
        pitchers = pitching[['Name', 'Team', 'Pos']].copy()
        pitchers['Type'] = 'pitcher'
        pitchers = pitchers.rename(columns={'Pos': 'Primary_Pos'})
        
        all_players = pd.concat([batters, pitchers], ignore_index=True)
        all_players['Display'] = all_players['Name'] + " (" + all_players['Type'].str[0].str.upper() + ", " + all_players['Team'].fillna('FA') + ")"
        all_players = all_players.sort_values('Name').drop_duplicates('Name')
        return all_players
    except Exception as e:
        st.error(f"Error loading players: {e}")
        return pd.DataFrame()

players_df = get_all_players(year)

st.header("Build Your Roster")
st.info("Type to search players (e.g., 'Judge', 'Ohtani', 'Skenes'). Select all players on your roster (starters + bench + IL).")

if players_df.empty:
    st.warning("No players loaded for this year. Try a different season or check internet connection.")
else:
    selected_displays = st.multiselect(
        "Search & add players to your roster",
        options=players_df['Display'].tolist(),
        default=[],
        placeholder="Start typing a name...",
        max_selections=40  # adjust if your league has very large rosters
    )

    roster = []
    for disp in selected_displays:
        row = players_df[players_df['Display'] == disp].iloc[0]
        name = row['Name']
        typ = row['Type']
        primary = row['Primary_Pos'] if pd.notna(row['Primary_Pos']) else ""
        
        positions = []
        if typ == 'batter':
            if 'OF' in str(primary).upper(): positions = ['OF', 'UTIL']
            elif primary in ['C', '1B', '2B', '3B', 'SS']: positions = [primary, 'UTIL']
            else: positions = ['UTIL']
        else:  # pitcher
            if 'SP' in str(primary).upper(): positions = ['SP', 'P']
            elif 'RP' in str(primary).upper(): positions = ['RP', 'P']
            else: positions = ['P']
        
        roster.append({
            "name": name,
            "type": typ,
            "positions": positions
        })

    if roster:
        st.success(f"Added {len(roster)} players. Ready to optimize!")
    else:
        st.info("Select players above to build your roster.")

# ────────────────────────────────────────────────
# Rest of your original code (points calculation + optimization)
# ────────────────────────────────────────────────

# Hardcoded standard lineup slots and eligibility (you can customize)
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
        return {}, 0.0
    prob = pulp.LpProblem("Lineup_Optimizer", pulp.LpMaximize)
    x = {}
    for i, p in enumerate(players):
        for s in slots:
            if set(p['positions']) & set(eligible[s]):
                x[(i, s)] = pulp.LpVariable(f"x_{i}_{s}", cat='Binary')
    # Objective
    prob += pulp.lpSum(x[(i, s)] * players[i]['points'] for i, s in x.keys())
    # Fill slots exactly
    for s in slots:
        prob += pulp.lpSum(x.get((i, s), 0) for i in range(len(players))) == slots[s]
    # Each player at most once
    for i in range(len(players)):
        prob += pulp.lpSum(x.get((i, s), 0) for s in slots) <= 1
    # Solve
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    lineup = {s: [] for s in slots}
    for var in x:
        if x[var].value() == 1:
            i, s = var
            lineup[s].append(f"{players[i]['name']} ({players[i]['points']:.2f} pts)")
    total_points = pulp.value(prob.objective) or 0.0
    return lineup, total_points

if st.button("Suggest Lineup"):
    if not roster:
        st.error("Please select at least some players first.")
    else:
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
