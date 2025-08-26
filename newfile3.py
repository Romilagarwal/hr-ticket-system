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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = dash.Dash(__name__)
app.title = "Pack UPH Trend Dashboard"

PLAN_VS_PRODUCTION_JSON = "plan_vs_production_complete.json"

json_lock = Lock()

SHIFTS = [
    ("06:00", "14:00", 6.71, "A", "#8e44ad"),  
    ("14:00", "22:00", 7.13, "B", "#4ECDC4"),
    ("22:00", "06:00", 7.63, "C", "#45B7D1"),
]

AREA_LINES = {
    "1A & 1B": ["LINE15", "LINE16", "LINE17", "LINE18", "LINE19", "LINE20", "LINE5", "LINE6", "LINE7"],
    "2A & 2B": ["LINE1", "LINE2", "LINE3", "LINE4", "LINE10", "LINE27", "LINE28", "LINE33", "LINE35"],
    "3A & 3B": ["LINE21", "LINE22", "LINE23", "LINE24", "LINE25", "LINE9", "LINE12", "LINE13", "LINE11", "LINE30", "LINE32", "LINE36", "LINE34"],
    "4": ["LINE32", "LINE36"],
    "5": ["LINE34", "LINE35"],
    "6": ["LINE27", "LINE28", "LINE30", "LINE33"],
    "C-Pack": ["LINE1","LINE2","LINE3","LINE4"],
    "f-Pack": ["LINE17","LINE18","LINE20","LINE33","LINE3","LINE28"],
    "Dual-Cell": ["LINE5"]
}

scheduler = BackgroundScheduler()
scheduler.start()

def create_db_connection():
    try:
        dsn = cx_Oracle.makedsn("172.19.0.105", "1521", service_name="BISDB")
        connection = cx_Oracle.connect(user="sa", password="sa", dsn=dsn)
        logger.info("✅ Database connection established")
        return connection
    except Exception as e:
        logger.error(f"❌ Database connection failed: {str(e)}")
        raise

def get_shift_from_time(test_time):
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

def get_packing_data_by_date(connection, selected_date, line_no=None, proj_code=None, area=None, view_type='daily', force_refresh=False):    
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
            return df

        df.columns = df.columns.str.lower()
        
        df['test_time'] = df['test_time'].apply(lambda x: x.replace(tzinfo=pytz.timezone('Asia/Kolkata')) if x.tzinfo is None else x)
        
        if area and area != 'all' and area in AREA_LINES:
            valid_lines = AREA_LINES[area]
            df = df[df['line_no'].isin(valid_lines)]
        
        if df.empty:
            return df

        shift_info = df['test_time'].apply(get_shift_from_time)
        df['shift'] = [info[0] for info in shift_info]
        df['available_hours'] = [info[1] for info in shift_info]
        df['shift_color'] = [info[2] for info in shift_info]
        
        df = df.dropna(subset=['shift'])
        
        logger.info(f"✅ Retrieved data for {selected_date} - {len(df)} records")
        return df
        
    except Exception as e:
        logger.error(f"❌ Query execution failed: {str(e)}")
        raise

def get_dropdown_options(connection, selected_date, selected_area):
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
            uph = total_packing
            
            hourly_metrics.append({
                'Hour': hour.strftime('%H:%M'),
                'Shift': shift,
                'Total_Packing_Count': total_packing,
                'UPH': uph,
                'Color': shift_color
            })
    
    return pd.DataFrame(hourly_metrics)

def calculate_weekly_uph(df):
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

def update_plan_vs_production_json_complete():
    try:
        start_time = dt.now()
        logger.info(f"🌅 Starting complete morning update at {start_time}")
        
        end_date = dt.now()
        start_date = end_date - timedelta(days=7)
        date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        
        connection = create_db_connection()
        complete_data = []
        
        for single_date in date_range:
            formatted_date = single_date.strftime('%d-%b-%Y')
            date_str = single_date.strftime('%d_%m_%y')
            
            logger.info(f"📅 Processing date: {formatted_date}")
            
            try:
                query = """
                SELECT 
                    SUM(CASE PROC_NAME WHEN 'CARTON' THEN 1 ELSE 0 END) AS TOTAL_PRODUCTION
                FROM sa.NVT_PROCESS_STATUS
                WHERE PROC_NAME = 'CARTON' 
                    AND TEST_TIME >= TO_DATE(:start_date, 'dd-mon-yyyy hh24:mi')
                    AND TEST_TIME < TO_DATE(:end_date, 'dd-mon-yyyy hh24:mi')
                    AND LINE_NO IS NOT NULL
                """
                
                start_datetime = f"{formatted_date} 06:00"
                end_datetime = (single_date + timedelta(days=1)).strftime('%d-%b-%Y') + " 06:00"
                
                result = pd.read_sql(query, connection, params={
                    "start_date": start_datetime,
                    "end_date": end_datetime
                })
                
                total_production = result['TOTAL_PRODUCTION'].iloc[0] if not result.empty and result['TOTAL_PRODUCTION'].iloc[0] else 0
                logger.info(f"✅ Production data for {formatted_date}: {total_production}")
                
            except Exception as e:
                logger.error(f"❌ Error fetching production data for {formatted_date}: {e}")
                total_production = 0
            
            total_plan_qty = 0
            file_found = False
            data_source = "no_file"
            
            possible_files = [
                f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{date_str}_ppc.xlsx",
            ]
            
            for target_file in possible_files:
                if os.path.exists(target_file):
                    try:
                        targets_df = pd.read_excel(target_file)
                        if 'Plan Qty' in targets_df.columns:
                            total_plan_qty = targets_df['Plan Qty'].sum()
                            file_found = True
                            data_source = "excel_file"
                            logger.info(f"✅ Plan file found: {target_file}, Plan Qty: {total_plan_qty}")
                            break
                    except Exception as e:
                        logger.warning(f"⚠️ Error reading file {target_file}: {e}")
                        continue
            
            if not file_found:
                logger.warning(f"⚠️ No plan file found for {formatted_date}")
            
            achievement_pct = (total_production / total_plan_qty * 100) if total_plan_qty > 0 else 0
            
            complete_data.append({
                "Date": formatted_date,
                "Plan_Qty": int(total_plan_qty),
                "Production_Qty": int(total_production),
                "Achievement_Pct": round(achievement_pct, 2),
                "file_found": file_found,
                "data_source": data_source
            })
        
        connection.close()
        
        json_structure = {
            "metadata": {
                "last_updated": start_time.strftime('%Y-%m-%d %H:%M:%S'),
                "update_status": "success",
                "total_days": len(complete_data),
                "next_update": (start_time + timedelta(days=1)).replace(hour=6, minute=0, second=0).strftime('%Y-%m-%d %H:%M:%S'),
                "processing_time_seconds": (dt.now() - start_time).total_seconds()
            },
            "data": complete_data
        }
        
        with json_lock:
            with open(PLAN_VS_PRODUCTION_JSON, 'w') as f:
                json.dump(json_structure, f, indent=2)
        
        end_time = dt.now()
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info(f"✅ Complete morning update finished at {end_time}")
        logger.info(f"⏱️ Processing time: {processing_time:.2f} seconds")
        logger.info(f"📊 Updated {len(complete_data)} days of data")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error in complete morning update: {str(e)}")
        
        error_structure = {
            "metadata": {
                "last_updated": dt.now().strftime('%Y-%m-%d %H:%M:%S'),
                "update_status": "error",
                "error_message": str(e),
                "total_days": 0,
                "next_update": (dt.now() + timedelta(days=1)).replace(hour=6, minute=0, second=0).strftime('%Y-%m-%d %H:%M:%S')
            },
            "data": []
        }
        
        with json_lock:
            with open(PLAN_VS_PRODUCTION_JSON, 'w') as f:
                json.dump(error_structure, f, indent=2)
        
        return False

def get_plan_vs_production_from_json_with_fallback():
    try:
        with json_lock:
            if not os.path.exists(PLAN_VS_PRODUCTION_JSON):
                logger.warning("⚠️ Plan vs production JSON file not found, querying database")
                return get_plan_vs_production_from_database()
            
            with open(PLAN_VS_PRODUCTION_JSON, 'r') as f:
                json_data = json.load(f)
        
        metadata = json_data.get('metadata', {})
        last_updated = metadata.get('last_updated', 'Unknown')
        update_status = metadata.get('update_status', 'Unknown')
        
        if update_status == 'error':
            logger.warning("⚠️ JSON update failed, falling back to database")
            return get_plan_vs_production_from_database()
        
        logger.info(f"📖 Reading JSON data - Last updated: {last_updated}, Status: {update_status}")
        
        data = json_data.get('data', [])
        if not data:
            logger.warning("⚠️ No data found in JSON file, querying database")
            return get_plan_vs_production_from_database()
        
        df = pd.DataFrame(data)
        
        required_columns = ['Date', 'Plan_Qty', 'Production_Qty', 'Achievement_Pct']
        for col in required_columns:
            if col not in df.columns:
                df[col] = 0
        
        df = df.sort_values('Date')
        
        logger.info(f"✅ Loaded {len(df)} days of data from JSON")
        return df[required_columns]
        
    except Exception as e:
        logger.error(f"❌ Error reading JSON file: {str(e)}, falling back to database")
        return get_plan_vs_production_from_database()

def get_plan_vs_production_from_database():
    try:
        end_date = dt.now()
        start_date = end_date - timedelta(days=7)
        date_range = pd.date_range(start=start_date, end=end_date - timedelta(days=1), freq='D')
        
        connection = create_db_connection()
        plan_qty_list = []
        production_qty_list = []
        achievement_pct_list = []
        dates = []
        
        for single_date in date_range:
            formatted_date = single_date.strftime('%d-%b-%Y')
            date_str = single_date.strftime('%d_%m_%y')
            
            df = get_packing_data_by_date(connection, formatted_date, line_no=None, proj_code=None, area=None, view_type='daily')
            total_production = df['packingcount'].sum() if not df.empty else 0
            
            total_plan_qty = 0
            target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{date_str}_ppc.xlsx"
            
            if os.path.exists(target_file):
                try:
                    targets_df = pd.read_excel(target_file)
                    if 'Plan Qty' in targets_df.columns:
                        total_plan_qty = targets_df['Plan Qty'].sum()
                except Exception as e:
                    logger.warning(f"⚠️ Error reading plan file {target_file}: {e}")
            
            achievement_pct = (total_production / total_plan_qty * 100) if total_plan_qty > 0 else 0
            
            dates.append(formatted_date)
            production_qty_list.append(total_production)
            plan_qty_list.append(total_plan_qty)
            achievement_pct_list.append(achievement_pct)
        
        connection.close()
        
        df = pd.DataFrame({
            'Date': dates,
            'Plan_Qty': plan_qty_list,
            'Production_Qty': production_qty_list,
            'Achievement_Pct': achievement_pct_list
        })
        
        logger.info(f"✅ Retrieved {len(df)} days of data from database fallback")
        return df.sort_values('Date')
        
    except Exception as e:
        logger.error(f"❌ Error in database fallback: {str(e)}")
        return pd.DataFrame({'Date': [], 'Plan_Qty': [], 'Production_Qty': [], 'Achievement_Pct': []})

scheduler.remove_all_jobs()

scheduler.add_job(
    update_plan_vs_production_json_complete,
    'cron',
    hour=6,
    minute=0,
    timezone='Asia/Kolkata',
    id='morning_update',
    max_instances=1,
    coalesce=True
)

scheduler.add_job(
    update_plan_vs_production_json_complete,
    'cron',
    hour=10,
    minute=30,
    timezone='Asia/Kolkata',
    id='backup_update',
    max_instances=1,
    coalesce=True
)

logger.info("✅ Scheduled updates: 6:00 AM (main), 10:30 AM (backup)")

app.layout = html.Div([
    html.Div(style={
        'position': 'fixed',
        'top': '0',
        'left': '0',
        'width': '100%',
        'height': '100%',
        'background': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        'zIndex': '-1'
    }),
    dcc.Interval(
        id='interval-component',
        interval=2*60*1000,
        n_intervals=0
    ),
    
    dcc.Interval(
        id='line-cycle-interval',
        interval=20*1000,
        n_intervals=0,
        disabled=True
    ),
    
    html.Div([
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
                dcc.Graph(
                    id='uph-chart', 
                    style={'height': '500px','width':'100%'},
                    config={
                        'responsive': True,
                        'displayModeBar': True,
                        'modeBarButtonsToRemove': ['lasso2d', 'select2d'],
                        'displaylogo': False
        })
            ], style={
                'background': 'linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%)',
                'padding': '20px',
                'borderRadius': '20px',
                'boxShadow': '0 20px 40px rgba(0,0,0,0.1)',
                'margin': '20px',
                'border': '1px solid rgba(255,255,255,0.2)',
                'zIndex': '300',
                'overflow':'hidden'
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
                'display': 'flex',
                'flexDirection': 'row',
                'justifyContent': 'space-between',
                'alignItems': 'stretch',
                'gap': '15px',
                'marginTop': '20px',
                'flexWrap': 'nowrap',
                'overflowX': 'auto'
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
        
        if area != 'all' and area != 'Select...' and line_no == 'all' and view_type == 'hourly' and area in AREA_LINES:
            df = pd.DataFrame()
            for line in AREA_LINES[area]:
                line_df = get_packing_data_by_date(connection, formatted_date, line, proj_code_query, area, view_type, force_refresh=force_refresh)
                df = pd.concat([df, line_df], ignore_index=True)
            
            if line_no_query:
                df = df[df['line_no'] == line_no_query]
        else:
            df = get_packing_data_by_date(connection, formatted_date, line_no_query, proj_code_query, area, view_type, force_refresh=force_refresh)

        plan_vs_prod_df = get_plan_vs_production_from_json_with_fallback()
        
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
        else:
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

        uph_fig = create_uph_chart(metrics_df, entity_col, title_prefix, target_uphs, plan_qtys, view_type, line_no, area)
        supporting_fig = create_supporting_charts(metrics_df, entity_col, view_type)
        stats_cards = create_stats_cards(metrics_df, line_no if line_no != 'all' else None, formatted_date)
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
        autosize=True,
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor='rgba(248,249,250,0.8)',
        paper_bgcolor='rgba(248,249,250,0.8)',
        showlegend=False
    )
    
    return fig

def create_uph_chart(df, entity_col, title_prefix, target_uphs=None, plan_qtys=None, view_type=None,line_no=None,area=None):
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
        autosize=True,
        margin=dict(l=50, r=50, t=80, b=50),  # Update margins
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
        autosize=True,
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor='rgba(248,249,250,0.8)',
        paper_bgcolor='rgba(248,249,250,0.8)',
        font=dict(family="Poppins", size=10)
    )

    return fig

def get_planned_upph_from_excel(line_no=None, date_str=None):    
    if not line_no or line_no == 'all':
        return "**Select specific line for Planned UPPH**"
    try:
        if date_str:
            date_obj = dt.strptime(date_str, '%d-%b-%Y')
            file_date = date_obj.strftime('%d_%m_%y')
        else:
            today = dt.now()
            file_date = today.strftime('%d_%m_%y')
        
        target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{file_date}_ppc.xlsx"
        
        if not os.path.exists(target_file):
            logger.warning(f"Target file not found: {target_file}")
            return "**No plan file found**"
        
        targets_df = pd.read_excel(target_file)
        logger.info(f"✅ Reading planned UPPH data from {target_file}")
        line_data = targets_df[targets_df['LINE'] == line_no]
        
        if line_data.empty:
            logger.warning(f"No data found for line {line_no}")
            return f"**No data for {line_no}**"       
        shift_timings = {'A': 6.71, 'B': 7.13, 'C': 7.63}        
        planned_upph_values = []        
        for shift in ['A', 'B', 'C']:
            shift_data = line_data[line_data['Shift'] == shift]            
            if not shift_data.empty:
                planned_qty = shift_data['Plan Qty'].iloc[0] if 'Plan Qty' in shift_data.columns else 0
                manpower = shift_data['MP'].iloc[0] if 'MP' in shift_data.columns else 0
                work_time = shift_timings[shift]               
                if manpower > 0 and work_time > 0:
                    planned_upph = planned_qty / (manpower * work_time)
                    planned_upph_values.append(f"**Shift {shift}:** {planned_upph:.2f}")
                    logger.info(f"✅ Shift {shift} Planned UPPH: {planned_upph:.2f} (Qty: {planned_qty}, MP: {manpower}, Time: {work_time})")
                else:
                    planned_upph_values.append(f"**Shift {shift}:** N/A")
                    logger.warning(f"⚠️ Shift {shift}: Invalid data (MP: {manpower}, Time: {work_time})")
            else:
                planned_upph_values.append(f"**Shift {shift}:** No Data")        
        if planned_upph_values:
            result = "<br>".join(planned_upph_values)
            logger.info(f"✅ Final planned UPPH calculation: {result}")
            return result
        else:
            return "**No planned UPPH data**"            
    except Exception as e:
        logger.error(f"❌ Error calculating planned UPPH: {str(e)}")
        return f"**Error: {str(e)}**"

def get_manpower_from_excel(line_no, formatted_date):
    try:
        if formatted_date:
            date_obj = dt.strptime(formatted_date, '%d-%b-%Y')
            file_date = date_obj.strftime('%d_%m_%y')
        else:
            today = dt.now()
            file_date = today.strftime('%d_%m_%y')
        
        target_file = f"\\\\svbanas\\temp_bin$\\1.PPC Plan for COE Report\\UPH_{file_date}_ppc.xlsx"
        logger.info(f"🔍 Looking for manpower file: {target_file}")
        
        if not os.path.exists(target_file):
            logger.warning(f"⚠️ Manpower file not found: {target_file}")
            return "**File Not Found**"
        
        targets_df = pd.read_excel(target_file)
        logger.info(f"✅ Successfully read Excel file with {len(targets_df)} rows")
        logger.info(f"📋 Available columns: {list(targets_df.columns)}")
        
        if 'MP' not in targets_df.columns:
            logger.warning("⚠️ 'MP' column not found in Excel file")
            return "**MP Column Missing**"
        
        if line_no and line_no != 'all':
            line_data = targets_df[targets_df['LINE'] == line_no]
            logger.info(f"🏭 Filtered data for line {line_no}: {len(line_data)} rows")
            
            if line_data.empty:
                return f"**No data for {line_no}**"
            
            mp_values = []
            for shift in ['A', 'B', 'C']:
                shift_data = line_data[line_data['Shift'] == shift]
                if not shift_data.empty:
                    mp = shift_data['MP'].iloc[0]
                    if pd.notna(mp) and mp > 0:
                        mp_values.append(f"**Shift {shift}:** {int(mp)}")
            
            return "<br>".join(mp_values) if mp_values else f"**No MP data for {line_no}**"
        
        else:
            logger.info("📊 Calculating aggregated manpower for all lines")
            
            mp_values = []
            for shift in ['A', 'B', 'C']:
                shift_data = targets_df[targets_df['Shift'] == shift]
                if not shift_data.empty:
                    mp_total = shift_data['MP'].fillna(0).sum()
                    if mp_total > 0:
                        mp_values.append(f"**Shift {shift}:** {int(mp_total)}")
                        logger.info(f"✅ Shift {shift} total MP: {int(mp_total)}")
            
            if mp_values:
                result = "<br>".join(mp_values)
                logger.info(f"✅ Final aggregated manpower: {result}")
                return result
            else:
                return "**No MP data available**"
    
    except Exception as e:
        logger.error(f"❌ Error in get_manpower_from_excel: {str(e)}")
        return f"**Error: {str(e)}**"

def create_stats_cards(df, line_no, formatted_date):
    cards = []
    
    total_production = df['Total_Packing_Count'].sum()
    cards.append(create_stat_card("📦 Total Production", f"{total_production:,}", "#3498db"))
    
    avg_uph = df['UPH'].mean() if not df.empty else 0
    cards.append(create_stat_card("⚡ Average UPH", f"{avg_uph:.2f}", "#FFC0CB"))
    
    peak_uph = df['UPH'].max() if not df.empty else 0
    cards.append(create_stat_card("🏆 Peak UPH", f"{peak_uph:.2f}", "#f39c12"))
    
    try:
        mp_html = get_manpower_from_excel(line_no, formatted_date)
        cards.append(create_stat_card("👷 Planned Manpower", mp_html, "#2980b9", allow_html=True))
        
    except Exception as e:
        logger.error(f"Error creating manpower card: {str(e)}")
        cards.append(create_stat_card("👷 Planned Manpower", "**Error Loading**", "#2980b9", allow_html=True))
    
    try:
        planned_upph_html = get_planned_upph_from_excel(line_no, formatted_date)
        cards.append(create_stat_card("🎯 Planned UPPH", planned_upph_html, "#9b59b6", allow_html=True))
        logger.info(f"✅ Added Planned UPPH card for {line_no}")
    except Exception as e:
        logger.error(f"Error creating planned UPPH card: {str(e)}")
        cards.append(create_stat_card("🎯 Planned UPPH", "**Error Loading**", "#9b59b6", allow_html=True))

    return cards

def create_stat_card(title, value, color, allow_html=False):
    return html.Div([
        html.Div([
            html.H4(
                value if not allow_html else html.Div([
                    dcc.Markdown(value, dangerously_allow_html=True)
                ]), 
                style={
                    'margin': '0',
                    'color': color,
                    'fontSize': '1.5rem' if allow_html else '2rem',
                    'fontWeight': '700',
                    'fontFamily': 'Poppins, sans-serif',
                    'lineHeight': '1.4' if allow_html else '1.2'
                }
            ),
            html.P(title, style={
                'margin': '5px 0 0 0',
                'color': '#7f8c8d',
                'fontSize': '0.9rem',
                'fontFamily': 'Poppins, sans-serif'
            })
        ])
    ], style={
        'background': f'linear-gradient(135deg, {color}15 0%, {color}25 100%)',
        'padding': '20px',  
        'borderRadius': '15px',
        'textAlign': 'center',
        'border': f'2px solid {color}30',
        'boxShadow': f'0 10px 25px {color}20',
        'transition': 'transform 0.3s ease',
        'cursor': 'pointer',
        'minHeight': '120px',
        'display': 'flex',
        'alignItems': 'center',
        'justifyContent': 'center',
        'flex': '1',  
        'minWidth': '180px'
    })

def create_plan_vs_production_chart(plan_vs_prod_df):
    if plan_vs_prod_df.empty:
        return create_empty_figure("No data available for 7-day plan vs production")
    # Convert Date column to datetime if it's not already
    if not pd.api.types.is_datetime64_any_dtype(plan_vs_prod_df['Date']):
        plan_vs_prod_df['Date'] = pd.to_datetime(plan_vs_prod_df['Date'], format='%d-%b-%Y')
    
    # Sort by date chronologically
    plan_vs_prod_df = plan_vs_prod_df.sort_values('Date').reset_index(drop=True)

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
        autosize=True,
        margin=dict(l=50, r=50, t=80, b=50),  # Update margins
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

    fig.update_xaxes(categoryorder='array',categoryarray=sorted(plan_vs_prod_df['Date'].unique()),showgrid=True, gridwidth=1, gridcolor='rgba(255,255,255,0.3)', tickfont=dict(color='#FFFFFF'))
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

def create_data_table(df, entity_col):
    if df.empty:
        return html.Div("No data available for table", style={'textAlign': 'center', 'color': '#7f8c8d'})
    
    df_display = df.copy()
    df_display['UPH'] = df_display['UPH'].round(2)
    
    return dash_table.DataTable(
        data=df_display.to_dict('records'),
        columns=[
            {"name": entity_col, "id": entity_col},
            {"name": "Shift", "id": "Shift"},
            {"name": "UPH", "id": "UPH", "type": "numeric", "format": {"specifier": ".2f"}},
            {"name": "Total Packing Count", "id": "Total_Packing_Count", "type": "numeric"}
        ],
        style_cell={
            'textAlign': 'center',
            'fontFamily': 'Poppins, sans-serif',
            'fontSize': '14px',
            'padding': '15px'
        },
        style_header={
            'backgroundColor': '#3498db',
            'color': 'white',
            'fontWeight': 'bold',
            'border': '1px solid #2980b9',
            'fontSize': '16px'
        },
        style_data={
            'backgroundColor': '#ffffff',
            'border': '1px solid #bdc3c7'
        },
        style_data_conditional=[
            {
                'if': {'row_index': 'odd'},
                'backgroundColor': '#f8f9fa'
            }
        ],
        sort_action="native",
        filter_action="native",
        page_size=10
    )

def create_info_panel(date, view_type, area, line_no, proj_code, record_count, total_packing, target_uphs=None):
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

if __name__ == '__main__':
    logger.info("🚀 Starting Pack UPH Dashboard...")
    
    if not os.path.exists(PLAN_VS_PRODUCTION_JSON):
        logger.info("📊 Initializing plan vs production data...")
        update_plan_vs_production_json_complete()
    
    app.run(debug=False, host='172.19.66.141', port=9021)
