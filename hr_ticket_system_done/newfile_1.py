import dash
import os
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import cx_Oracle
import datetime
import pytz
from datetime import timedelta, datetime as dt
import logging
import hashlib
from apscheduler.schedulers.background import BackgroundScheduler
from dash import dcc, html, Input, Output, dash_table, no_update
from threading import Lock

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Dash app
app = dash.Dash(__name__)
app.title = "Pack UPH Trend Dashboard"

# In-memory cache for all query results and daily totals
data_cache = {}
daily_totals_cache = {}  # Cache for daily total packing counts
MAX_CACHE_SIZE = 100  # Maximum number of cached queries

# JSON file path for 7-day plan vs production data
JSON_FILE_PATH = "plan_vs_production.json"

# Thread lock for safe JSON file access
json_lock = Lock()

# Enhanced shift definitions with colors
SHIFTS = [
    ("06:00", "14:00", 6.71, "A", "#8e44ad"),  # A Shift, 6 AM to 2 PM
    ("14:00", "22:00", 7.13, "B", "#4ECDC4"),  # B Shift, 2 PM to 10 PM
    ("22:00", "06:00", 7.63, "C", "#45B7D1"),  # C Shift, 10 PM to 6 AM next day
]

# Area to line mappings
AREA_LINES = {
    "1A & 1B": ["LINE15", "LINE16", "LINE17", "LINE18", "LINE19", "LINE20", "LINE5", "LINE6", "LINE7"],
    "2A & 2B": ["LINE1", "LINE2", "LINE3", "LINE4", "LINE10", "LINE27", "LINE28", "LINE33", "LINE35"],
    "3A & 3B": ["LINE21", "LINE22", "LINE23", "LINE24", "LINE25", "LINE9", "LINE12", "LINE13", "LINE11", "LINE30", "LINE32", "LINE36", "LINE34"],
    "4": ["LINE32", "LINE36"],
    "5": ["LINE34", "LINE35"],
    "6": ["LINE27", "LINE28", "LINE30", "LINE33"]
}

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Database connection function
def create_db_connection():
    """Create database connection"""
    try:
        dsn = cx_Oracle.makedsn("172.19.0.105", "1521", service_name="BISDB")
        connection = cx_Oracle.connect(user="sa", password="sa", dsn=dsn)
        logger.info("✅ Database connection established")
        return connection
    except Exception as e:
        logger.error(f"❌ Database connection failed: {str(e)}")
        raise

def get_shift_from_time(test_time):
    """Determine shift based on TEST_TIME"""
    test_hour = test_time.hour
    test_minute = test_time.minute
    test_time_minutes = test_hour * 60 + test_minute

    for start_time, end_time, hours, shift_name, color in SHIFTS:
        start_hour, start_minute = map(int, start_time.split(":"))
        end_hour, end_minute = map(int, end_time.split(":"))
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute

        if shift_name == "C":
            if test_time_minutes >= start_minutes or test_time_minutes < end_minutes:
                return shift_name, hours, color
        else:
            if start_minutes <= test_time_minutes < end_minutes:
                return shift_name, hours, color
    return None, None, None

def get_cache_key(selected_date, line_no, proj_code, area, view_type):
    """Generate a cache key based on query parameters"""
    key_string = f"{selected_date}_{line_no or 'all'}_{proj_code or 'all'}_{area or 'all'}_{view_type}"
    return hashlib.md5(key_string.encode()).hexdigest()

def get_packing_data_by_date(connection, selected_date, line_no=None, proj_code=None, area=None, view_type='daily', force_refresh=False):
    """Get packing data for a specific date or week with line and model information"""
    cache_key = get_cache_key(selected_date, line_no, proj_code, area, view_type)
    
    if force_refresh and cache_key in data_cache:
        logger.info(f"🗑️ Clearing cache for key: {cache_key} due to interval refresh")
        del data_cache[cache_key]
    
    if cache_key in data_cache:
        logger.info(f"✅ Retrieved data from cache for key: {cache_key}")
        return data_cache[cache_key]
    
    start_datetime = f"{selected_date} 06:00"
    is_weekly = view_type == 'weekly'
    if is_weekly:
        end_date = dt.strptime(selected_date, '%d-%b-%Y').replace(tzinfo=pytz.timezone('Asia/Kolkata'))
        start_date = (end_date - timedelta(days=7)).strftime('%d-%b-%Y')
        start_datetime = f"{start_date} 06:00"
        end_datetime = (end_date + timedelta(days=1)).strftime('%d-%b-%Y') + " 06:00"
    else:
        end_datetime = (dt.strptime(selected_date, '%d-%b-%Y').replace(tzinfo=pytz.timezone('Asia/Kolkata')) + timedelta(days=1)).strftime('%d-%b-%Y') + " 06:00"
    
    query = """
    SELECT 
        SUM(CASE PROC_NAME WHEN 'CARTON' THEN 1 ELSE 0 END) AS PACKINGCOUNT,
        LINE_NO,
        PROJ_CODE,
        TEST_TIME,
        TRUNC(TEST_TIME - INTERVAL '6' HOUR) AS SHIFT_DATE
    FROM sa.NVT_PROCESS_STATUS 
    WHERE NVT_PROCESS_STATUS.PROC_NAME = 'CARTON'
      AND TEST_TIME >= TO_DATE(:start_date, 'dd-mon-yyyy hh24:mi')
      AND TEST_TIME < TO_DATE(:end_date, 'dd-mon-yyyy hh24:mi')
      AND (:line_no IS NULL OR LINE_NO = :line_no)
      AND (:proj_code IS NULL OR PROJ_CODE = :proj_code)
      AND LINE_NO IS NOT NULL
      AND PROJ_CODE IS NOT NULL
    GROUP BY LINE_NO, PROJ_CODE, TEST_TIME, TRUNC(TEST_TIME - INTERVAL '6' HOUR)
    ORDER BY TEST_TIME
    """
    
    try:
        df = pd.read_sql(query, connection, params={
            "start_date": start_datetime,
            "end_date": end_datetime,
            "line_no": line_no,
            "proj_code": proj_code
        })
        
        logger.info(f"📊 DataFrame shape: {df.shape}")
        
        if df.empty:
            data_cache[cache_key] = df
            return df
            
        df.columns = df.columns.str.lower()
        
        # Ensure test_time is timezone-aware
        df['test_time'] = df['test_time'].apply(lambda x: x.replace(tzinfo=pytz.timezone('Asia/Kolkata')) if x.tzinfo is None else x)
        
        # Filter by area if specified
        if area and area != 'all' and area in AREA_LINES:
            valid_lines = AREA_LINES[area]
            df = df[df['line_no'].isin(valid_lines)]
        
        if df.empty:
            data_cache[cache_key] = df
            return df
            
        shift_info = df['test_time'].apply(get_shift_from_time)
        df['shift'] = [info[0] for info in shift_info]
        df['available_hours'] = [info[1] for info in shift_info]
        df['shift_color'] = [info[2] for info in shift_info]
        
        df = df.dropna(subset=['shift'])
        
        if view_type == 'weekly':
            df['date'] = df['test_time'].dt.date
            daily_totals = df.groupby('date')['packingcount'].sum().to_dict()
            for date, total in daily_totals.items():
                date_str = date.strftime('%d-%b-%Y')
                daily_totals_cache[date_str] = total
                logger.info(f"✅ Cached daily total for {date_str}: {total}")
        
        if len(data_cache) >= MAX_CACHE_SIZE:
            oldest_key = next(iter(data_cache))
            data_cache.pop(oldest_key)
            logger.info(f"🗑️ Removed oldest cache entry: {oldest_key}")
        
        data_cache[cache_key] = df
        logger.info(f"✅ Cached data for key: {cache_key}")
        
        return df
    except Exception as e:
        logger.error(f"❌ Query execution failed: {str(e)}")
        raise

def get_dropdown_options(connection, selected_date, selected_area):
    """Get unique line_no and proj_code values for dropdowns, and area options from mapping"""
    try:
        query = """
        SELECT DISTINCT LINE_NO, PROJ_CODE
        FROM sa.NVT_PROCESS_STATUS
        WHERE PROC_NAME = 'CARTON'
          AND TEST_TIME >= TO_DATE(:start_date, 'dd-mon-yyyy hh24:mi')
          AND TEST_TIME < TO_DATE(:end_date, 'dd-mon-yyyy hh24:mi')
          AND LINE_NO IS NOT NULL
          AND PROJ_CODE IS NOT NULL
        """
        start_datetime = f"{selected_date} 06:00"
        end_datetime = (dt.strptime(selected_date, '%d-%b-%Y') + timedelta(days=1)).strftime('%d-%b-%Y') + " 06:00"

        df = pd.read_sql(query, connection, params={
            "start_date": start_datetime,
            "end_date": end_datetime
        })

        area_options = [{'label': 'All Areas', 'value': 'all'}] + [
            {'label': area, 'value': area} for area in sorted(AREA_LINES.keys())
        ]

        # Filter line options based on selected area
        if selected_area and selected_area != 'all' and selected_area in AREA_LINES:
            valid_lines = AREA_LINES[selected_area]
            line_options = [{'label': 'All Lines', 'value': 'all'}] + [
                {'label': line, 'value': line} for line in sorted(set(valid_lines)) if line in df['LINE_NO'].unique()
            ]
        else:
            line_options = [{'label': 'All Lines', 'value': 'all'}] + [
                {'label': line, 'value': line} for line in sorted(df['LINE_NO'].unique())
            ]

        proj_options = [{'label': 'All Models', 'value': 'all'}] + [
            {'label': proj, 'value': proj} for proj in sorted(df['PROJ_CODE'].unique())
        ]

        return area_options, line_options, proj_options
    except Exception as e:
        logger.error(f"❌ Dropdown options query failed: {str(e)}")
        return ([{'label': 'All Areas', 'value': 'all'}],
                [{'label': 'All Lines', 'value': 'all'}],
                [{'label': 'All Models', 'value': 'all'}])

def calculate_line_wise_uph(df):
    """Calculate line-wise UPH for each shift"""
    line_metrics = []

    for shift in ['A', 'B', 'C']:
        shift_data = df[df['shift'] == shift]
        if shift_data.empty:
            continue

        shift_hours = shift_data['available_hours'].iloc[0]
        shift_color = shift_data['shift_color'].iloc[0]

        for line in shift_data['line_no'].unique():
            line_data = shift_data[shift_data['line_no'] == line]
            total_packing = line_data['packingcount'].sum()

            uph = total_packing / shift_hours if shift_hours > 0 else 0

            line_metrics.append({
                'Line': line,
                'Shift': shift,
                'Total_Packing_Count': total_packing,
                'UPH': uph,
                'Color': shift_color
            })

    return pd.DataFrame(line_metrics)

def calculate_model_wise_uph(df):
    """Calculate model-wise UPH for each shift"""
    model_metrics = []

    for shift in ['A', 'B', 'C']:
        shift_data = df[df['shift'] == shift]
        if shift_data.empty:
            continue

        shift_hours = shift_data['available_hours'].iloc[0]
        shift_color = shift_data['shift_color'].iloc[0]

        for model in shift_data['proj_code'].unique():
            model_data = shift_data[shift_data['proj_code'] == model]
            total_packing = model_data['packingcount'].sum()

            uph = total_packing / shift_hours if shift_hours > 0 else 0

            model_metrics.append({
                'Model': model,
                'Shift': shift,
                'Total_Packing_Count': total_packing,
                'UPH': uph,
                'Color': shift_color
            })

    return pd.DataFrame(model_metrics)

def calculate_hourly_uph(df):
    """Calculate hourly UPH"""
    df['hour'] = df['test_time'].dt.floor('h')
    hourly_metrics = []

    for shift in ['A', 'B', 'C']:
        shift_data = df[df['shift'] == shift]
        if shift_data.empty:
            continue

        shift_color = shift_data['shift_color'].iloc[0]

        for hour in shift_data['hour'].unique():
            hour_data = shift_data[shift_data['hour'] == hour]
            total_packing = hour_data['packingcount'].sum()
            uph = total_packing  # UPH per hour (1 hour duration)

            hourly_metrics.append({
                'Hour': hour.strftime('%H:%M'),
                'Shift': shift,
                'Total_Packing_Count': total_packing,
                'UPH': uph,
                'Color': shift_color
            })

    return pd.DataFrame(hourly_metrics)

def calculate_weekly_uph(df):
    """Calculate daily UPH for the week"""
    df['date'] = df['test_time'].dt.date
    daily_metrics = []

    for date in df['date'].unique():
        date_data = df[df['date'] == date]
        for shift in ['A', 'B', 'C']:
            shift_data = date_data[date_data['shift'] == shift]
            if shift_data.empty:
                continue

            shift_hours = shift_data['available_hours'].iloc[0]
            shift_color = shift_data['shift_color'].iloc[0]
            total_packing = shift_data['packingcount'].sum()
            uph = total_packing / shift_hours if shift_hours > 0 else 0

            daily_metrics.append({
                'Date': date,
                'Shift': shift,
                'Total_Packing_Count': total_packing,
                'UPH': uph,
                'Color': shift_color
            })

    return pd.DataFrame(daily_metrics)

def update_plan_vs_production_json():
    """Update the JSON file with 7-day plan vs production data at 6 AM daily"""
    try:
        end_date = dt.now()
        start_date = end_date - timedelta(days=7)
        date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        
        # Read existing JSON data to preserve historical data
        existing_df = pd.DataFrame({'Date': [], 'Plan_Qty': [], 'Production_Qty': [], 'Achievement_Pct': []})
        with json_lock:
            if os.path.exists(JSON_FILE_PATH):
                with open(JSON_FILE_PATH, 'r') as f:
                    existing_data = json.load(f)
                existing_df = pd.DataFrame(existing_data)
                existing_df['Date'] = pd.to_datetime(existing_df['Date'], format='%d-%b-%Y').dt.strftime('%d-%b-%Y')
        
        # Determine which days need updating (only the latest day if JSON is up-to-date)
        if not existing_df.empty:
            latest_json_date = pd.to_datetime(existing_df['Date'], format='%d-%b-%Y').max()
            start_date = max(start_date, latest_json_date + timedelta(days=1))
            date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        
        if len(date_range) == 0 and not existing_df.empty:
            logger.info("✅ JSON file is up-to-date, no new data to fetch")
            return existing_df.sort_values('Date')
        
        connection = create_db_connection()
        plan_qty_list = []
        production_qty_list = []
        achievement_pct_list = []
        dates = []
        
        for single_date in date_range:
            formatted_date = single_date.strftime('%d-%b-%Y')
            date_str = single_date.strftime('%d_%m_%y')
            
            if formatted_date in daily_totals_cache:
                total_production = daily_totals_cache[formatted_date]
                logger.info(f"✅ Retrieved daily total from cache for {formatted_date}: {total_production}")
            else:
                df = get_packing_data_by_date(connection, formatted_date, line_no=None, proj_code=None, area=None, view_type='daily')
                total_production = df['packingcount'].sum() if not df.empty else 0
                daily_totals_cache[formatted_date] = total_production
                logger.info(f"✅ Cached daily total for {formatted_date}: {total_production}")
            
            total_plan_qty = 0
            target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{date_str}_ppc.xlsx"
            if os.path.exists(target_file):
                targets_df = pd.read_excel(target_file)
                logger.info(f"✅ Read UPH targets from {target_file}")
                if 'Plan Qty' in targets_df.columns:
                    total_plan_qty = targets_df['Plan Qty'].sum()
            
            achievement_pct = (total_production / total_plan_qty * 100) if total_plan_qty > 0 else 0
            
            dates.append(formatted_date)
            production_qty_list.append(total_production)
            plan_qty_list.append(total_plan_qty)
            achievement_pct_list.append(achievement_pct)
        
        connection.close()
        
        new_df = pd.DataFrame({
            'Date': dates,
            'Plan_Qty': plan_qty_list,
            'Production_Qty': production_qty_list,
            'Achievement_Pct': achievement_pct_list
        })
        
        # Combine existing and new data
        if not existing_df.empty:
            plan_vs_prod_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            plan_vs_prod_df = new_df
        
        # Filter to keep only the last 7 days
        date_range_str = [d.strftime('%d-%b-%Y') for d in pd.date_range(start=end_date - timedelta(days=7), end=end_date - timedelta(days=1), freq='D')]
        plan_vs_prod_df = plan_vs_prod_df[plan_vs_prod_df['Date'].isin(date_range_str)]
        
        with json_lock:
            with open(JSON_FILE_PATH, 'w') as f:
                json.dump(plan_vs_prod_df.to_dict('records'), f, indent=2)
            logger.info(f"✅ Updated JSON file with 7-day plan vs production data at {dt.now()}")
        
        return plan_vs_prod_df.sort_values('Date')
    except Exception as e:
        logger.error(f"❌ Error updating 7-day plan vs production JSON: {str(e)}")
        return pd.DataFrame({'Date': [], 'Plan_Qty': [], 'Production_Qty': [], 'Achievement_Pct': []})

def get_plan_vs_production_from_json():
    """Read 7-day plan vs production data from JSON file and fetch remaining days from database"""
    try:
        # Initialize empty DataFrame
        result_df = pd.DataFrame({'Date': [], 'Plan_Qty': [], 'Production_Qty': [], 'Achievement_Pct': []})
        
        # Read JSON file if it exists
        json_data = []
        with json_lock:
            if os.path.exists(JSON_FILE_PATH):
                with open(JSON_FILE_PATH, 'r') as f:
                    json_data = json.load(f)
                logger.info("✅ Retrieved data from JSON file")
        
        if json_data:
            json_df = pd.DataFrame(json_data)
            json_df['Date'] = pd.to_datetime(json_df['Date'], format='%d-%b-%Y').dt.strftime('%d-%b-%Y')
            result_df = json_df
            
            # Determine the latest date in JSON
            latest_json_date = pd.to_datetime(json_df['Date'], format='%d-%b-%Y').max()
            end_date = dt.now()
            date_range = pd.date_range(start=latest_json_date + timedelta(days=1), end=end_date - timedelta(days=1), freq='D')
        else:
            # If JSON is empty or not found, fetch all 7 days from database
            end_date = dt.now()
            start_date = end_date - timedelta(days=7)
            date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        
        if len(date_range) > 0:
            connection = create_db_connection()
            plan_qty_list = []
            production_qty_list = []
            achievement_pct_list = []
            dates = []
            
            for single_date in date_range:
                formatted_date = single_date.strftime('%d-%b-%Y')
                date_str = single_date.strftime('%d_%m_%y')
                
                if formatted_date in daily_totals_cache:
                    total_production = daily_totals_cache[formatted_date]
                    logger.info(f"✅ Retrieved daily total from cache for {formatted_date}: {total_production}")
                else:
                    df = get_packing_data_by_date(connection, formatted_date, line_no=None, proj_code=None, area=None, view_type='daily')
                    total_production = df['packingcount'].sum() if not df.empty else 0
                    daily_totals_cache[formatted_date] = total_production
                    logger.info(f"✅ Cached daily total for {formatted_date}: {total_production}")
                
                total_plan_qty = 0
                target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{date_str}_ppc.xlsx"
                if os.path.exists(target_file):
                    targets_df = pd.read_excel(target_file)
                    logger.info(f"✅ Read UPH targets from {target_file}")
                    if 'Plan Qty' in targets_df.columns:
                        total_plan_qty = targets_df['Plan Qty'].sum()
                
                achievement_pct = (total_production / total_plan_qty * 100) if total_plan_qty > 0 else 0
                
                dates.append(formatted_date)
                production_qty_list.append(total_production)
                plan_qty_list.append(total_plan_qty)
                achievement_pct_list.append(achievement_pct)
            
            connection.close()
            
            # Create DataFrame for remaining days
            db_df = pd.DataFrame({
                'Date': dates,
                'Plan_Qty': plan_qty_list,
                'Production_Qty': production_qty_list,
                'Achievement_Pct': achievement_pct_list
            })
            
            # Combine JSON and database data
            if not json_df.empty:
                result_df = pd.concat([json_df, db_df], ignore_index=True)
            else:
                result_df = db_df
        
        # Ensure the DataFrame covers exactly 7 days
        end_date = dt.now()
        start_date = end_date - timedelta(days=7)
        date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        date_range_str = [d.strftime('%d-%b-%Y') for d in date_range]
        result_df = result_df[result_df['Date'].isin(date_range_str)]
        
        logger.info("✅ Compiled 7-day plan vs production data")
        return result_df.sort_values('Date')
    except Exception as e:
        logger.error(f"❌ Error compiling plan vs production data: {str(e)}")
        return pd.DataFrame({'Date': [], 'Plan_Qty': [], 'Production_Qty': [], 'Achievement_Pct': []})

# Schedule daily JSON update at 6 AM IST
scheduler.add_job(
    update_plan_vs_production_json,
    'cron',
    hour=6,
    minute=0,
    timezone='Asia/Kolkata'
)

# Define the app layout
app.layout = html.Div([
    # Background gradient
    html.Div(style={
        'position': 'fixed',
        'top': '0',
        'left': '0',
        'width': '100%',
        'height': '100%',
        'background': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        'zIndex': '-1'
    }),

    # Interval component for 15-minute updates
    dcc.Interval(
        id='interval-component',
        interval=2*60*1000,  # 15 minutes in milliseconds
        n_intervals=0
    ),

    # Interval component for line cycling in hourly view
    dcc.Interval(
        id='line-cycle-interval',
        interval=20*1000,  # 20 seconds in milliseconds
        n_intervals=0,
        disabled=True
    ),

    # Main container
    html.Div([
        # Header Section
        html.Div([
            html.Div([
                html.H1("📊 Pack UPH Analytics Dashboard",
                       style={
                           'textAlign': 'center',
                           'color': '#ffffff',
                           'margin': '0',
                           'fontSize': '2.5rem',
                           'fontWeight': '700',
                           'textShadow': '2px 2px 4px rgba(0,0,0,0.3)',
                           'fontFamily': 'Poppins, sans-serif'
                       }),
                html.P("Real-time Pack UPH analysis with advanced visualization",
                       style={
                           'textAlign': 'center',
                           'color': '#e8f4fd',
                           'fontSize': '1.2rem',
                           'margin': '10px 0 0 0',
                           'fontFamily': 'Poppins, sans-serif'
                       })
            ], style={
                'background': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                'padding': '40px 20px',
                'borderRadius': '20px',
                'boxShadow': '0 20px 40px rgba(0,0,0,0.15)',
                'margin': '20px',
                'border': '1px solid rgba(255,255,255,0.1)'
            })
        ]),

        # Control Panel
        html.Div([
            html.Div([
                html.H3("🎛️ Control Panel", style={
                    'color': '#2c3e50',
                    'margin': '0 0 20px 0',
                    'fontFamily': 'Poppins, sans-serif',
                    'fontWeight': '600'
                }),
                html.Div([
                    html.Div([
                        html.Label("📅 Select Date:", style={
                            'color': '#34495e',
                            'fontWeight': '600',
                            'marginBottom': '10px',
                            'display': 'block',
                            'fontFamily': 'Poppins, sans-serif'
                        }),
                        dcc.DatePickerSingle(
                            id='date-picker',
                            date=datetime.date.today(),
                            display_format='DD-MMM-YYYY',
                            style={
                                'width': '100%',
                                'borderRadius': '10px',
                                'border': '2px solid #3498db',
                                'boxShadow': '0 5px 15px rgba(52,152,219,0.2)',
                                'zIndex': '10000'
                            }
                        )
                    ], style={'flex': '1', 'marginRight': '20px'}),
                    html.Div([
                        html.Label("🌍 Select Area:", style={
                            'color': '#34495e',
                            'fontWeight': '600',
                            'marginBottom': '10px',
                            'display': 'block',
                            'fontFamily': 'Poppins, sans-serif'
                        }),
                        dcc.Dropdown(
                            id='area-selector',
                            value='all',
                            style={
                                'borderRadius': '10px',
                                'border': '2px solid #3498db',
                                'boxShadow': '0 5px 15px rgba(231,76,60,0.2)',
                                'zIndex': '10000'
                            }
                        )
                    ], style={'flex': '1', 'marginRight': '20px'}),
                    html.Div([
                        html.Label("🏭 Select Line:", style={
                            'color': '#34495e',
                            'fontWeight': '600',
                            'marginBottom': '10px',
                            'display': 'block',
                            'fontFamily': 'Poppins, sans-serif'
                        }),
                        dcc.Dropdown(
                            id='line-selector',
                            value='all',
                            style={
                                'borderRadius': '10px',
                                'border': '2px solid #3498db',
                                'boxShadow': '0 5px 15px rgba(231,76,60,0.2)',
                                'zIndex': '10000'
                            }
                        )
                    ], style={'flex': '1', 'marginRight': '20px'}),
                    html.Div([
                        html.Label("📋 Select Model:", style={
                            'color': '#34495e',
                            'fontWeight': '600',
                            'marginBottom': '10px',
                            'display': 'block',
                            'fontFamily': 'Poppins, sans-serif'
                        }),
                        dcc.Dropdown(
                            id='proj-code-selector',
                            value='all',
                            style={
                                'borderRadius': '10px',
                                'border': '2px solid #3498db',
                                'boxShadow': '0 5px 15px rgba(231,76,60,0.2)',
                                'zIndex': '10000'
                            }
                        )
                    ], style={'flex': '1', 'marginRight': '20px'}),
                    html.Div([
                        html.Label("👁️ Select View:", style={
                            'color': '#34495e',
                            'fontWeight': '600',
                            'marginBottom': '10px',
                            'display': 'block',
                            'fontFamily': 'Poppins, sans-serif'
                        }),
                        dcc.Dropdown(
                            id='view-selector',
                            options=[
                                {'label': '📈 Line-wise View', 'value': 'line'},
                                {'label': '🏭 Model-wise View', 'value': 'model'},
                                {'label': '📅 Weekly View', 'value': 'weekly'},
                                {'label': '🕒 Hourly View', 'value': 'hourly'}
                            ],
                            value='line',
                            style={
                                'borderRadius': '10px',
                                'border': '2px solid #3498db',
                                'boxShadow': '0 5px 15px rgba(231,76,60,0.2)',
                                'zIndex': '10000'
                            }
                        )
                    ], style={'flex': '1', 'marginRight': '20px'}),
                    html.Div([
                        html.Button(
                            "🔄 Refresh Data",
                            id='refresh-btn',
                            n_clicks=0,
                            style={
                                'background': 'linear-gradient(135deg, #2ecc71 0%, #27ae60 100%)',
                                'color': 'white',
                                'border': 'none',
                                'padding': '12px 24px',
                                'borderRadius': '10px',
                                'cursor': 'pointer',
                                'fontSize': '16px',
                                'fontWeight': '600',
                                'boxShadow': '0 5px 15px rgba(46,204,113,0.3)',
                                'transition': 'all 0.3s ease',
                                'fontFamily': 'Poppins, sans-serif',
                                'marginTop': '25px'
                            }
                        )
                    ], style={'flex': '0 0 auto'})
                ], style={
                    'display': 'flex',
                    'alignItems': 'end',
                    'gap': '15px',
                    'flexWrap': 'wrap'
                })
            ], style={
                'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
                'padding': '30px',
                'borderRadius': '20px',
                'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
                'margin': '20px',
                'border': '1px solid rgba(255,255,255,0.2)',
                'zIndex': '5000',
                'position': 'relative'
            })
        ]),

        html.Div([
            html.Div(id='info-panel', style={
                'padding': '20px',
                'backgroundColor': '#e8f6f3',
                'borderRadius': '15px',
                'border': '2px solid #16a085',
                'boxShadow': '0 10px 25px rgba(22,160,133,0.15)',
                'zIndex': '400'
            })
        ], style={'margin': '20px'}),

        html.Div([
            html.Div([
                dcc.Graph(id='uph-chart', style={'height': '500px'})
            ], style={
                'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
                'padding': '20px',
                'borderRadius': '20px',
                'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
                'margin': '20px',
                'border': '1px solid rgba(255,255,255,0.2)',
                'zIndex': '300'
            }),

            html.Div([
                dcc.Graph(id='supporting-charts', style={'height': '600px'})
            ], style={
                'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
                'padding': '20px',
                'borderRadius': '20px',
                'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
                'margin': '20px',
                'border': '1px solid rgba(255,255,255,0.2)',
                'zIndex': '300'
            })
        ]),

        html.Div([
            html.H3("📊 Key Performance Metrics", style={
                'color': '#2c3e50',
                'margin': '0 0 20px 0',
                'fontFamily': 'Poppins, sans-serif',
                'fontWeight': '600',
                'textAlign': 'center'
            }),
            html.Div(id='stats-cards', style={
                'display': 'grid',
                'gridTemplateColumns': 'repeat(auto-fit, minmax(250px, 1fr))',
                'gap': '20px',
                'marginTop': '20px'
            })
        ], style={
            'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
            'padding': '30px',
            'borderRadius': '20px',
            'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
            'margin': '20px',
            'border': '1px solid rgba(255,255,255,0.2)',
            'zIndex': '300'
        }),

        html.Div([
            html.H3("📋 Detailed Data Analysis", style={
                'color': '#2c3e50',
                'margin': '0 0 20px 0',
                'fontFamily': 'Poppins, sans-serif',
                'fontWeight': '600',
                'textAlign': 'center'
            }),
            html.Div(id='data-table')
        ], style={
            'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
            'padding': '30px',
            'borderRadius': '20px',
            'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
            'margin': '20px',
            'border': '1px solid rgba(255,255,255,0.2)',
            'zIndex': '300'
        }),

        html.Div([
            html.H3("📈 7-Day Plan vs Production Trend", style={
                'color': '#2c3e50',
                'margin': '0 0 20px 0',
                'fontFamily': 'Poppins, sans-serif',
                'fontWeight': '600',
                'textAlign': 'center'
            }),
            dcc.Graph(id='plan-vs-production-chart', style={'height': '600px'})
        ], style={
            'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
            'padding': '30px',
            'borderRadius': '20px',
            'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
            'margin': '20px',
            'border': '1px solid rgba(255,255,255,0.2)',
            'zIndex': '300'
        })
    ], style={
        'minHeight': '100vh',
        'fontFamily': 'Poppins, sans-serif'
    })
])

# Store input values to persist between refresh clicks and line cycling
app.clientside_callback(
    """
    function(n_clicks, n_intervals, date, view, area, line, proj) {
        sessionStorage.setItem('dashboard_date', date);
        sessionStorage.setItem('dashboard_view', view);
        sessionStorage.setItem('dashboard_area', area);
        sessionStorage.setItem('dashboard_line', line);
        sessionStorage.setItem('dashboard_proj', proj);
        return [date, view, area, line, proj];
    }
    """,
    [Output('date-picker', 'date', allow_duplicate=True),
     Output('view-selector', 'value', allow_duplicate=True),
     Output('area-selector', 'value', allow_duplicate=True),
     Output('line-selector', 'value', allow_duplicate=True),
     Output('proj-code-selector', 'value', allow_duplicate=True)],
    [Input('refresh-btn', 'n_clicks'),
     Input('line-cycle-interval', 'n_intervals'),
     Input('date-picker', 'date'),
     Input('view-selector', 'value'),
     Input('area-selector', 'value'),
     Input('line-selector', 'value'),
     Input('proj-code-selector', 'value')],
    prevent_initial_call='initial_duplicate'
)

# Client-side callback to cycle through lines in hourly view
app.clientside_callback(
    """
    function(n_intervals, view_type, area, line_options) {
        if (view_type !== 'hourly' || area === 'all' || !line_options || line_options.length <= 1) {
            return [true, window.dash_clientside.no_update];
        }
        const lines = line_options.map(opt => opt.value).filter(val => val !== 'all');
        if (lines.length === 0) {
            return [true, window.dash_clientside.no_update];
        }
        const currentIndex = lines.indexOf(sessionStorage.getItem('dashboard_line') || lines[0]);
        const nextIndex = (currentIndex + 1) % lines.length;
        const nextLine = lines[nextIndex];
        sessionStorage.setItem('dashboard_line', nextLine);
        return [false, nextLine];
    }
    """,
    [Output('line-cycle-interval', 'disabled'),
     Output('line-selector', 'value', allow_duplicate=True)],
    [Input('line-cycle-interval', 'n_intervals'),
     Input('view-selector', 'value'),
     Input('area-selector', 'value'),
     Input('line-selector', 'options')],
     prevent_initial_call='initial_duplicate'
)

# Callback to update dropdown options
@app.callback(
    [Output('area-selector', 'options'),
     Output('line-selector', 'options'),
     Output('proj-code-selector', 'options')],
    [Input('refresh-btn', 'n_clicks'),
     Input('interval-component', 'n_intervals'),
     Input('area-selector', 'value')],
    [dash.dependencies.State('date-picker', 'date')]
)
def update_dropdown_options(n_clicks, n_intervals, selected_area, selected_date):
    if n_clicks is None and n_intervals == 0:
        raise dash.exceptions.PreventUpdate
    try:
        date_obj = dt.strptime(selected_date, '%Y-%m-%d')
        formatted_date = date_obj.strftime('%d-%b-%Y')
        connection = create_db_connection()
        area_options, line_options, proj_options = get_dropdown_options(connection, formatted_date, selected_area)
        connection.close()
        return area_options, line_options, proj_options
    except Exception as e:
        logger.error(f"Error updating dropdown options: {str(e)}")
        return ([{'label': 'All Areas', 'value': 'all'}],
                [{'label': 'All Lines', 'value': 'all'}],
                [{'label': 'All Models', 'value': 'all'}])

@app.callback(
    [Output('uph-chart', 'figure'),
     Output('supporting-charts', 'figure'),
     Output('stats-cards', 'children'),
     Output('data-table', 'children'),
     Output('info-panel', 'children'),
     Output('plan-vs-production-chart', 'figure')],
    [Input('refresh-btn', 'n_clicks'),
     Input('interval-component', 'n_intervals'),
     Input('line-cycle-interval', 'n_intervals')],
    [dash.dependencies.State('date-picker', 'date'),
     dash.dependencies.State('view-selector', 'value'),
     dash.dependencies.State('area-selector', 'value'),
     dash.dependencies.State('line-selector', 'value'),
     dash.dependencies.State('proj-code-selector', 'value')]
)
def update_dashboard(n_clicks, n_intervals, line_cycle_intervals, selected_date, view_type, area, line_no, proj_code):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None
    force_refresh = triggered_id == 'interval-component'

    if n_clicks is None and n_intervals == 0 and line_cycle_intervals == 0:
        raise dash.exceptions.PreventUpdate
    try:
        date_obj = dt.strptime(selected_date, '%Y-%m-%d')
        formatted_date = date_obj.strftime('%d-%b-%Y')
        
        connection = create_db_connection()
        line_no_query = None if line_no == 'all' else line_no
        proj_code_query = None if proj_code == 'all' else proj_code
        
        # If an area is selected and line is 'all' in hourly view, prepare data for cycling
        if area != 'all' and line_no == 'all' and view_type == 'hourly' and area in AREA_LINES:
            df = pd.DataFrame()
            for line in AREA_LINES[area]:
                line_df = get_packing_data_by_date(connection, formatted_date, line, proj_code_query, area, view_type, force_refresh=force_refresh)
                df = pd.concat([df, line_df], ignore_index=True)
            # Filter for the current line in the cycle
            if line_no_query:
                df = df[df['line_no'] == line_no_query]
        else:
            df = get_packing_data_by_date(connection, formatted_date, line_no_query, proj_code_query, area, view_type, force_refresh=force_refresh)
        
        plan_vs_prod_df = get_plan_vs_production_from_json()
        
        connection.close()
        
        if df.empty:
            empty_fig = create_empty_figure("No data available for selected parameters")
            return (empty_fig, empty_fig, [], 
                   html.Div("No data available", style={'textAlign': 'center', 'color': '#7f8c8d'}),
                   create_info_panel(formatted_date, view_type, area, line_no_query, proj_code_query, 0, 0),
                   empty_fig)
        
        logger.info(f"Processing view_type: {view_type} for date: {formatted_date}")
        if view_type == 'line':
            metrics_df = calculate_line_wise_uph(df)
            entity_col = 'Line'
            title_prefix = 'Line-wise'
            logger.info(f"Line-wise UPH data updated - Records: {len(metrics_df)}")
        elif view_type == 'model':
            metrics_df = calculate_model_wise_uph(df)
            entity_col = 'Model'
            title_prefix = 'Model-wise'
        elif view_type == 'hourly':
            metrics_df = calculate_hourly_uph(df)
            entity_col = 'Hour'
            title_prefix = 'Hourly'
        else:  # weekly
            metrics_df = calculate_weekly_uph(df)
            entity_col = 'Date'
            title_prefix = 'Weekly'
        
        target_uphs = {}
        plan_qtys = {}
        if line_no != 'all' and view_type in ['line', 'hourly']:
            for shift in ['A', 'B', 'C']:
                target_uph, plan_qty = get_target_uph(line_no, formatted_date, shift)
                if target_uph is not None:
                    target_uphs[shift] = target_uph
                    logger.info(f"Target UPH for {line_no}, {formatted_date}, Shift {shift}: {target_uph}")
                if plan_qty is not None:
                    plan_qtys[shift] = plan_qty
                    logger.info(f"Plan Qty for {line_no}, {formatted_date}, Shift {shift}: {plan_qty}")
        
        uph_fig = create_uph_chart(metrics_df, entity_col, title_prefix, target_uphs, plan_qtys, view_type,line_no,area)
        supporting_fig = create_supporting_charts(metrics_df, entity_col, view_type)
        stats_cards = create_stats_cards(metrics_df)
        data_table = create_data_table(metrics_df, entity_col)
        info_panel = create_info_panel(formatted_date, view_type, area, line_no_query, proj_code_query, 
                                     len(metrics_df), df['packingcount'].sum(), target_uphs)
        plan_vs_prod_fig = create_plan_vs_production_chart(plan_vs_prod_df)
        
        return uph_fig, supporting_fig, stats_cards, data_table, info_panel, plan_vs_prod_fig
        
    except Exception as e:
        logger.error(f"Error in dashboard update: {str(e)}")
        empty_fig = create_empty_figure(f"Error: {str(e)}")
        return (empty_fig, empty_fig, [], 
               html.Div("Error loading data", style={'textAlign': 'center', 'color': '#3498db'}),
               html.Div("Error loading info", style={'textAlign': 'center', 'color': '#3498db'}),
               empty_fig)

def get_target_uph(line_no=None, date_str=None, shift=None):
    """Get target UPH and Plan Qty values from daily Excel file"""
    if not line_no or line_no == 'all':
        return None, None

    try:
        if date_str:
            date_obj = dt.strptime(date_str, '%d-%b-%Y')
            file_date = date_obj.strftime('%d_%m_%y')
        else:
            today = dt.now()
            file_date = today.strftime('%d_%m_%y')

        target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{file_date}_ppc.xlsx"

        logger.info(f"Looking for UPH target file: {target_file}")

        if os.path.exists(target_file):
            targets_df = pd.read_excel(target_file)
            logger.info(f"✅ Read UPH targets from {target_file}")

            targets_df = targets_df[targets_df['LINE'] == line_no]

            if shift:
                targets_df = targets_df[targets_df['Shift'] == shift]

            if not targets_df.empty:
                target_uph = targets_df['UPH'].iloc[0]
                plan_qty = targets_df['Plan Qty'].iloc[0] if 'Plan Qty' in targets_df.columns else None
                return target_uph, plan_qty

            logger.warning(f"No target found for line {line_no}, shift {shift}")
            return None, None
        else:
            logger.warning(f"Target UPH file not found: {target_file}")
            return None, None
    except Exception as e:
        logger.error(f"❌ Error reading target UPH file: {str(e)}")
        return None, None

def create_empty_figure(message):
    """Create an empty figure with message"""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=18, color='#7f8c8d'),
        bgcolor='rgba(255,255,255,0.8)',
        bordercolor='#bdc3c7',
        borderwidth=2
    )
    fig.update_layout(
        title="No Data Available",
        height=400,
        plot_bgcolor='rgba(248,249,250,0.8)',
        paper_bgcolor='rgba(248,249,250,0.8)',
        showlegend=False
    )
    return fig

def create_uph_chart(df, entity_col, title_prefix, target_uphs=None, plan_qtys=None, view_type=None,line_no=None,area=None):
    """Create the main UPH chart with shift-specific target lines and plan quantity achievement status"""
    fig = go.Figure()

    for shift in ['A', 'B', 'C']:
        shift_data = df[df['Shift'] == shift]
        if shift_data.empty:
            continue

        total_shift_packing = shift_data['Total_Packing_Count'].sum()
        logger.info(f"Shift {shift} - Total packing: {total_shift_packing}")

        fig.add_trace(go.Bar(
            x=shift_data[entity_col],
            y=shift_data['UPH'],
            name=f'Shift {shift}',
            marker_color=shift_data['Color'].iloc[0],
            text=[f'{val:.1f}' for val in shift_data['UPH']],
            textposition='auto',
            textfont=dict(size=12, color='white', family='Poppins'),
            hovertemplate=f'<b>%{{x}}</b><br>Shift {shift}<br>UPH: %{{y:.2f}}<br>Packing Count: %{{customdata}}<extra></extra>',
            customdata=shift_data['Total_Packing_Count']
        ))

        if target_uphs and shift in target_uphs:
            fig.add_shape(
                type='line',
                x0=0,
                y0=target_uphs[shift],
                x1=1,
                y1=target_uphs[shift],
                line=dict(
                    color=shift_data['Color'].iloc[0],
                    width=3,
                    dash='dash',
                ),
                xref='paper',
                yref='y',
                layer='below'
            )

            fig.add_annotation(
                x=1.01,
                y=target_uphs[shift],
                text=f"Target: {target_uphs[shift]:.1f}",
                showarrow=False,
                font=dict(color=shift_data['Color'].iloc[0], size=12),
                xref='paper',
                yref='y',
                xanchor='left',
                yanchor='middle'
            )

        if plan_qtys and shift in plan_qtys and len(shift_data) > 0:
            plan_qty = plan_qtys[shift]
            achieved = total_shift_packing >= plan_qty
            status_text = f"{'✅ Plan quantity achieved' if achieved else '❌ Plan quantity not achieved'}: {total_shift_packing}/{plan_qty}"

            if view_type == 'hourly':
                x_values = shift_data[entity_col].tolist()

                if len(x_values) > 0:
                    max_y = shift_data['UPH'].max() * 1.15
                    middle_idx = len(x_values) // 2
                    middle_x = x_values[middle_idx]
                    banner_width = min(300, len(x_values) * 80)

                    fig.add_annotation(
                        x=middle_x,
                        y=max_y,
                        text=status_text,
                        showarrow=False,
                        font=dict(
                            color="white",
                            size=14,
                            family="Poppins",
                            weight="bold"
                        ),
                        bgcolor="rgba(46, 204, 113, 0.8)" if achieved else "rgba(231, 76, 60, 0.8)",
                        bordercolor="white",
                        borderwidth=2,
                        borderpad=6,
                        xanchor="center",
                        yanchor="bottom",
                        opacity=0.95,
                        width=banner_width,
                        align="center"
                    )

            elif view_type == 'line':
                x_pos = shift_data[entity_col].iloc[0]
                bar_height = shift_data['UPH'].iloc[0]
                y_pos = bar_height + 20

                fig.add_annotation(
                    x=x_pos,
                    y=y_pos,
                    text=status_text,
                    showarrow=False,
                    font=dict(
                        color="white",
                        size=14,
                        family="Poppins",
                        weight="bold"
                    ),
                    bgcolor="rgba(46, 204, 113, 0.8)" if achieved else "rgba(231, 76, 60, 0.8)",
                    bordercolor="white",
                    borderwidth=2,
                    borderpad=6,
                    xanchor="center",
                    yanchor="bottom",
                    opacity=0.95,
                    align="center"
                )

    title_suffix = ""
    if target_uphs:
        title_suffix += " (with Target UPH"
        if plan_qtys:
            title_suffix += " & Plan Qty"
        title_suffix += ")"
    line_suffix = f" (Line: {line_no})" if view_type == 'hourly' and line_no and area and area != 'all' else ""        
    fig.update_layout(
        title={
            'text': f'{title_prefix} UPH Analysis by Shift{title_suffix}{line_suffix}',
            'x': 0.5,
            'font': {'size': 24, 'color': '#2c3e50', 'family': 'Poppins', 'weight': 'bold'}
        },
        xaxis_title=entity_col,
        yaxis_title="Units Per Hour (UPH)",
        height=500,
        plot_bgcolor='rgba(248,249,250,0.8)',
        paper_bgcolor='rgba(248,249,250,0.8)',
        font=dict(family="Poppins", size=12),
        barmode='group',
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(189,195,199,0.3)')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(189,195,199,0.3)')

    return fig

def create_supporting_charts(df, entity_col, view_type):
    """Create supporting charts"""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            'Total Packing Count by Shift',
            'Average UPH by Shift',
            f'UPH Distribution by {entity_col}',
            'Shift Performance Comparison'
        ),
        specs=[[{"secondary_y": False}, {"secondary_y": False}],
               [{"secondary_y": False}, {"secondary_y": False}]]
    )

    shift_totals = df.groupby('Shift').agg({
        'Total_Packing_Count': 'sum',
        'Color': 'first'
    }).reset_index()

    fig.add_trace(
        go.Bar(
            x=shift_totals['Shift'],
            y=shift_totals['Total_Packing_Count'],
            name='Packing Count',
            marker_color=shift_totals['Color'],
            showlegend=False
        ),
        row=1, col=1
    )

    shift_avg = df.groupby('Shift').agg({
        'UPH': 'mean',
        'Color': 'first'
    }).reset_index()

    fig.add_trace(
        go.Bar(
            x=shift_avg['Shift'],
            y=shift_avg['UPH'],
            name='Average UPH',
            marker_color=shift_avg['Color'],
            showlegend=False
        ),
        row=1, col=2
    )

    fig.add_trace(
        go.Histogram(
            x=df['UPH'],
            nbinsx=20,
            name='UPH Distribution',
            marker_color='#3498db',
            opacity=0.7,
            showlegend=False
        ),
        row=2, col=1
    )

    if view_type == 'weekly':
        fig.add_trace(
            go.Scatter(
                x=df['Date'],
                y=df['UPH'],
                mode='lines+markers',
                name='Daily UPH',
                line=dict(color='#3498db', width=3),
                marker=dict(size=10, color='#3498db'),
                showlegend=False
            ),
            row=2, col=2
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=shift_avg['Shift'],
                y=shift_avg['UPH'],
                mode='lines+markers',
                name='Shift Performance',
                line=dict(color='#3498db', width=3),
                marker=dict(size=10, color='#3498db'),
                showlegend=False
            ),
            row=2, col=2
        )

    fig.update_layout(
        height=600,
        showlegend=False,
        plot_bgcolor='rgba(248,249,250,0.8)',
        paper_bgcolor='rgba(248,249,250,0.8)',
        font=dict(family="Poppins", size=10)
    )

    return fig

def create_stats_cards(df):
    """Create statistics cards"""
    cards = []

    avg_uph = df['UPH'].mean()
    cards.append(create_stat_card("📊 Average UPH", f"{avg_uph:.1f}", "#2ecc71"))

    max_uph = df['UPH'].max()
    cards.append(create_stat_card("🚀 Maximum UPH", f"{max_uph:.1f}", "#3498db"))

    total_count = df['Total_Packing_Count'].sum()
    cards.append(create_stat_card("📦 Total Packing", f"{total_count:,}", "#9b59b6"))

    return cards

def create_plan_vs_production_chart(plan_vs_prod_df):
    """Create the 7-day plan vs production chart with achievement % on secondary y-axis"""
    if plan_vs_prod_df.empty:
        return create_empty_figure("No data available for 7-day plan vs production")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Bar(
            x=plan_vs_prod_df['Date'],
            y=plan_vs_prod_df['Plan_Qty'],
            name='Plan Qty',
            marker_color='#3498db',
            opacity=0.6,
            hovertemplate='<b>%{x}</b><br>Plan Qty: %{y}<extra></extra>'
        ),
        secondary_y=False
    )

    fig.add_trace(
        go.Bar(
            x=plan_vs_prod_df['Date'],
            y=plan_vs_prod_df['Production_Qty'],
            name='Production Qty',
            marker_color='#8e44ad',
            opacity=0.6,
            hovertemplate='<b>%{x}</b><br>Production Qty: %{y}<extra></extra>'
        ),
        secondary_y=False
    )

    fig.add_trace(
        go.Scatter(
            x=plan_vs_prod_df['Date'],
            y=plan_vs_prod_df['Achievement_Pct'],
            name='Achievement %',
            mode='lines+markers+text',
            line=dict(color='#FFFF00', width=3),
            marker=dict(size=8),
            text=[f'{pct:.1f}%' for pct in plan_vs_prod_df['Achievement_Pct']],
            textposition='top center',
            textfont=dict(size=12, color='#FFFF00', family='Poppins'),
            hovertemplate='<b>%{x}</b><br>Achievement: %{y:.1f}%<extra></extra>',
            showlegend=True
        ),
        secondary_y=True
    )

    fig.update_layout(
        title={
            'text': '7-Day Plan vs Production Quantity with Achievement %',
            'x': 0.5,
            'font': {'size': 24, 'color': '#FFFFFF', 'family': 'Poppins', 'weight': 'bold'}
        },
        xaxis_title='Date',
        yaxis_title='Quantity',
        yaxis2_title='Achievement %',
        height=600,
        plot_bgcolor='black',
        paper_bgcolor='black',
        font=dict(family="Poppins", size=12, color='#FFFFFF'),
        barmode='group',
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color='#FFFFFF')
        )
    )

    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(255,255,255,0.3)', tickfont=dict(color='#FFFFFF'))
    fig.update_yaxes(
        title_text="Quantity",
        showgrid=True,
        gridwidth=1,
        gridcolor='rgba(255,255,255,0.3)',
        tickfont=dict(color='#FFFFFF'),
        secondary_y=False
    )
    fig.update_yaxes(
        title_text="Achievement %",
        showgrid=False,
        range=[0, max(100, plan_vs_prod_df['Achievement_Pct'].max() * 1.2)],
        tickfont=dict(color='#FFFFFF'),
        secondary_y=True
    )

    return fig

def create_stat_card(title, value, color):
    """Create a single statistics card"""
    return html.Div([
        html.Div([
            html.H4(value, style={
                'margin': '0',
                'color': color,
                'fontSize': '2.5rem',
                'fontWeight': '700',
                'fontFamily': 'Poppins, sans-serif'
            }),
            html.P(title, style={
                'margin': '5px 0 0 0',
                'color': '#7f8c8d',
                'fontSize': '1rem',
                'fontFamily': 'Poppins, sans-serif'
            })
        ])
    ], style={
        'background': f'linear-gradient(135deg, {color}15 0%, {color}25 100%)',
        'padding': '25px',
        'borderRadius': '15px',
        'textAlign': 'center',
        'border': f'2px solid {color}30',
        'boxShadow': f'0 10px 25px {color}20',
        'transition': 'transform 0.3s ease',
        'cursor': 'pointer'
    })

def create_data_table(df, entity_col):
    """Create the data table"""
    table_df = df.copy()
    table_df['UPH'] = table_df['UPH'].round(2)

    return dash_table.DataTable(
        data=table_df.to_dict('records'),
        columns=[
            {'name': entity_col, 'id': entity_col},
            {'name': 'Shift', 'id': 'Shift'},
            {'name': 'UPH', 'id': 'UPH', 'type': 'numeric', 'format': {'specifier': '.2f'}},
            {'name': 'Total Packing Count', 'id': 'Total_Packing_Count', 'type': 'numeric'}
        ],
        style_cell={
            'textAlign': 'center',
            'padding': '15px',
            'fontFamily': 'Poppins, sans-serif',
            'fontSize': '14px'
        },
        style_header={
            'backgroundColor': '#3498db',
            'color': 'white',
            'fontWeight': 'bold',
            'fontSize': '16px'
        },
        style_data={
            'backgroundColor': '#f8f9fa',
            'color': '#2c3e50'
        },
        style_data_conditional=[
            {
                'if': {'row_index': 'odd'},
                'backgroundColor': 'white'
            }
        ],
        page_size=15,
        sort_action="native",
        filter_action="native"
    )

def create_info_panel(date, view_type, area, line_no, proj_code, record_count, total_packing, target_uphs=None):
    """Create the information panel"""
    view_name = {
        'line': 'Line-wise',
        'model': 'Model-wise',
        'hourly': 'Hourly',
        'weekly': 'Weekly'
    }.get(view_type, 'Unknown')

    info_elements = [
        html.Div([
            html.Strong("📅 Analysis Date: ", style={'color': '#2c3e50'}),
            html.Span(date, style={'color': '#3498db', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("🌍 Area: ", style={'color': '#2c3e50'}),
            html.Span(area or 'All', style={'color': '#3498db', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("🏭 Line: ", style={'color': '#2c3e50'}),
            html.Span(line_no or 'All', style={'color': '#3498db', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("📋 Model: ", style={'color': '#2c3e50'}),
            html.Span(proj_code or 'All', style={'color': '#3498db', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("👁️ Current View: ", style={'color': '#2c3e50'}),
            html.Span(view_name, style={'color': '#3498db', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("📊 Records Found: ", style={'color': '#2c3e50'}),
            html.Span(str(record_count), style={'color': '#27ae60', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("📦 Total Packing: ", style={'color': '#2c3e50'}),
            html.Span(f"{total_packing:,}", style={'color': '#9b59b6', 'fontWeight': '600'})
        ], style={'marginRight': '30px'}),

        html.Div([
            html.Strong("🕐 Last Updated: ", style={'color': '#2c3e50'}),
            html.Span(dt.now().strftime('%H:%M:%S'), style={'color': '#f39c12', 'fontWeight': '600'})
        ])
    ]

    if target_uphs and len(target_uphs) > 0:
        target_elements = []
        for shift, uph in target_uphs.items():
            target_elements.append(
                html.Div([
                    html.Strong(f"🎯 Shift {shift} Target: ", style={'color': '#2c3e50'}),
                    html.Span(f"{uph:.1f} UPH", style={'color': '#c0392b', 'fontWeight': '600'})
                ], style={'marginRight': '30px', 'marginTop': '10px'})
            )

        info_elements.append(
            html.Div(target_elements, style={
                'display': 'flex',
                'flexWrap': 'wrap',
                'width': '100%',
                'marginTop': '15px'
            })
        )

    return html.Div(info_elements, style={
        'display': 'flex',
        'flexWrap': 'wrap',
        'alignItems': 'center',
        'gap': '20px',
        'fontFamily': 'Poppins, sans-serif',
        'fontSize': '14px'
    })

# Enhanced CSS for hover effects and animations
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap');

            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: 'Poppins', sans-serif;
                overflow-x: hidden;
            }

            body::before {
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                z-index: -2;
                animation: gradientShift 10s ease infinite;
            }

            @keyframes gradientShift {
                0%, 100% { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
                50% { background: linear-gradient(135deg, #764ba2 0%, #667eea 100%); }
            }

            .particle {
                position: fixed;
                width: 4px;
                height: 4px;
                background: rgba(255, 255, 255, 0.3);
                border-radius: 50%;
                animation: float 20s linear infinite;
                z-index: -1;
            }

            @keyframes float {
                0% { transform: translateY(100vh) rotate(0deg); opacity: 0; }
                10% { opacity: 1; }
                90% { opacity: 1; }
                100% { transform: translateY(-100vh) rotate(360deg); opacity: 0; }
            }

            .card-hover {
                transition: all 0.3s ease;
                transform: translateY(0);
            }

            .card-hover:hover {
                transform: translateY(-5px);
                box-shadow: 0 25px 50px rgba(0,0,0,0.15) !important;
            }

            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 8px 20px rgba(46,204,113,0.4) !important;
            }

            .control-panel {
                position: relative;
                z-index: 5000 !important;
            }

            .dash-date-picker {
                position: relative !important;
                z-index: 9000 !important;
            }

            .DateInput_input {
                border-radius: 10px !important;
                border: 2px solid #3498db !important;
                box-shadow: 0 5px 15px rgba(52,152,219,0.2) !important;
                padding: 8px;
                font-family: 'Poppins', sans-serif;
            }

            .SingleDatePickerInput,
            .DateRangePicker,
            .SingleDatePicker {
                z-index: 9000 !important;
            }

            .SingleDatePickerOverlay,
            .DateRangePickerOverlay,
            .DayPickerPopper,
            .CalendarMonth,
            .CalendarMonthGrid,
            .DayPicker,
            .DayPicker_transitionContainer {
                z-index: 9999 !important;
            }

            .dash-dropdown {
                position: relative !important;
                z-index: 8000 !important;
            }

            .Select-control {
                border-radius: 10px !important;
                border: 2px solid #3498db !important;
                box-shadow: 0 5px 15px rgba(231,76,60,0.2) !important;
                font-family: 'Poppins', sans-serif;
            }

            .Select-menu-outer {
                z-index: 9999 !important;
                position: absolute !important;
                border-radius: 10px !important;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2) !important;
                font-family: 'Poppins', sans-serif;
                overflow-y: auto !important;
                max-height: 300px !important;
            }

            #react-select-dropdown-portal,
            #react-select-datepicker-portal,
            .ReactModal__Overlay,
            .ReactModal__Content {
                z-index: 9999 !important;
            }

            .js-plotly-plot {
                position: relative;
                z-index: 100;
            }

            .dash-graph,
            .dash-tab-content,
            .dash-spreadsheet-container {
                position: relative;
                z-index: 100;
            }

            .is-focused .Select-control,
            .is-open .Select-control {
                z-index: 9000 !important;
            }

            @media (max-width: 768px) {
                .grid {
                    grid-template-columns: 1fr !important;
                }

                .flex-container {
                    flex-direction: column !important;
                }

                .card {
                    margin: 10px !important;
                }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>

        <script>
            function createParticles() {
                const particleCount = 20;
                for (let i = 0; i < particleCount; i++) {
                    const particle = document.createElement('div');
                    particle.className = 'particle';
                    particle.style.left = Math.random() * 100 + '%';
                    particle.style.animationDelay = Math.random() * 20 + 's';
                    particle.style.animationDuration = (Math.random() * 10 + 10) + 's';
                    document.body.appendChild(particle);
                }
            }

            function addHoverEffects() {
                const cards = document.querySelectorAll('[style*="border-radius"]');
                cards.forEach(card => {
                    card.classList.add('card-hover');
                });
            }

            function fixControlPanelZIndex() {
                const controlPanels = document.querySelectorAll('.dash-dropdown, .dash-date-picker');
                controlPanels.forEach(panel => {
                    let parent = panel;
                    for (let i = 0; i < 3; i++) {
                        if (parent.parentElement) {
                            parent = parent.parentElement;
                        }
                    }
                    if (parent) {
                        parent.classList.add('control-panel');
                    }
                });
            }

            document.addEventListener('DOMContentLoaded', function() {
                createParticles();
                addHoverEffects();
                fixControlPanelZIndex();

                const observer = new MutationObserver(function() {
                    addHoverEffects();
                    fixControlPanelZIndex();
                });
                observer.observe(document.body, { childList: true, subtree: true });
            });

            document.addEventListener('click', function(e) {
                if (e.target.tagName === 'A' && e.target.getAttribute('href').startsWith('#')) {
                    e.preventDefault();
                    const target = document.querySelector(e.target.getAttribute('href'));
                    if (target) {
                        target.scrollIntoView({ behavior: 'smooth' });
                    }
                }
            });
        </script>
    </body>
</html>
'''

# Initialize JSON file on startup
update_plan_vs_production_json()

# Run the app
if __name__ == '__main__':
    app.run(debug=False, host='172.19.66.141', port=8852)
