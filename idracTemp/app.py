from flask import Flask, render_template, jsonify, request
import requests
from bs4 import BeautifulSoup
import smtplib
from apscheduler.schedulers.background import BackgroundScheduler
import logging
import json
import time
from datetime import datetime, timedelta
import urllib3
import re

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Configuration
class Config:
    IDRAC_URL = "https://10.129.16.81"
    IDRAC_USERNAME = "root"
    IDRAC_PASSWORD = "P@ssw0rd3128!"
    
    # SMTP Configuration
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "nxpisian@gmail.com"
    SENDER_PASSWORD = "aqkz uykr cmfu oqbm"
    RECEIVER_EMAIL = "supercompnxp@gmail.com"
    
    # Monitoring Configuration
    CHECK_INTERVAL_MINUTES = 60
    WARNING_THRESHOLD = 27
    CRITICAL_THRESHOLD = 30
    
    # Email Settings
    SEND_WARNING_EMAILS = True
    SEND_CRITICAL_EMAILS = True
    SEND_REGULAR_REPORTS = True

# Global variables
last_temperature = None
last_status = "UNKNOWN"
temperature_history = []
last_email_sent = {}
next_email_delay = 5  # Custom delay for next email in minutes
scheduled_email_job = 7 # Reference to scheduled email job

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('idrac_monitor.log'),
            logging.StreamHandler()
        ]
    )

class IDRACMonitor:
    def __init__(self):
        self.base_url = Config.IDRAC_URL
        self.username = Config.IDRAC_USERNAME
        self.password = Config.IDRAC_PASSWORD
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        
    def get_temperature(self):
        """Get temperature from iDRAC using multiple methods"""
        try:
            logging.info("Attempting to get temperature from iDRAC...")
            
            # Try multiple methods in sequence
            methods = [
                self._try_redfish_api,
                self._try_legacy_api, 
                self._try_html_parsing,
                self._try_sensor_api
            ]
            
            for method in methods:
                try:
                    temp, status = method()
                    if temp is not None:
                        logging.info(f"Successfully got temperature via {method.__name__}: {temp}°C")
                        return temp, status
                except Exception as e:
                    logging.warning(f"Method {method.__name__} failed: {str(e)}")
                    continue
            
            logging.error("All temperature retrieval methods failed")
            return None, "All retrieval methods failed"
            
        except Exception as e:
            logging.error(f"Temperature retrieval error: {str(e)}")
            return None, str(e)
    
    def _try_redfish_api(self):
        """Try Redfish API endpoints"""
        endpoints = [
            "/redfish/v1/Chassis/System.Embedded.1/Thermal",
            "/redfish/v1/Chassis/1/Thermal", 
            "/redfish/v1/Chassis/Self/Thermal",
            "/redfish/v1/Chassis/System.Embedded.1/Sensors"
        ]
        
        for endpoint in endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                response = self.session.get(url, auth=(self.username, self.password), timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    logging.info(f"Redfish response from {endpoint}: {json.dumps(data)[:500]}...")
                    
                    # Parse different Redfish structures
                    temp = self._parse_redfish_data(data)
                    if temp is not None:
                        return temp, self._get_temperature_status(temp)
                        
            except Exception as e:
                logging.warning(f"Redfish endpoint {endpoint} failed: {str(e)}")
                continue
                
        return None, "Redfish API unavailable"
    
    def _parse_redfish_data(self, data):
        """Parse temperature from various Redfish structures - WITH -60°C ADJUSTMENT"""
        logging.info(f"Parsing Redfish data structure...")
        
        # Method 1: Look for Temperatures array (most common)
        if 'Temperatures' in data:
            logging.info("Found Temperatures array, searching for valid temperature readings...")
            for sensor in data['Temperatures']:
                sensor_name = sensor.get('Name', 'Unknown')
                reading_c = sensor.get('ReadingCelsius')
                reading_raw = sensor.get('Reading')
                
                logging.info(f"Sensor: {sensor_name}, ReadingCelsius: {reading_c}, Reading: {reading_raw}")
                
                # Prefer ReadingCelsius if available
                if reading_c is not None:
                    # Apply -60°C adjustment and validate it's a reasonable temperature
                    adjusted_temp = reading_c - 62
                    if 0 <= adjusted_temp <= 100:  # Reasonable server temperature range
                        logging.info(f"Using adjusted ReadingCelsius: {reading_c}°C -> {adjusted_temp}°C from sensor: {sensor_name}")
                        return adjusted_temp
                
                # Fallback to Reading field
                if reading_raw is not None:
                    # Check if it's a temperature value (not RPM, voltage, etc.)
                    if isinstance(reading_raw, (int, float)):
                        adjusted_temp = reading_raw - 60
                        if 0 <= adjusted_temp <= 100:
                            logging.info(f"Using adjusted Reading: {reading_raw}°C -> {adjusted_temp}°C from sensor: {sensor_name}")
                            return adjusted_temp
                    elif isinstance(reading_raw, str):
                        # Extract numeric value from string
                        match = re.search(r'(\d+)', str(reading_raw))
                        if match:
                            temp = int(match.group(1))
                            adjusted_temp = temp - 60
                            if 0 <= adjusted_temp <= 100:
                                logging.info(f"Using adjusted parsed Reading: {temp}°C -> {adjusted_temp}°C from sensor: {sensor_name}")
                                return adjusted_temp
        
        # Method 2: Look for Readings array
        if 'Readings' in data:
            logging.info("Found Readings array, searching for temperature values...")
            for reading in data['Readings']:
                reading_value = reading.get('Reading')
                reading_name = reading.get('Name', 'Unknown')
                
                logging.info(f"Reading: {reading_name}, Value: {reading_value}")
                
                if reading_value is not None:
                    if isinstance(reading_value, (int, float)):
                        adjusted_temp = reading_value - 60
                        if 0 <= adjusted_temp <= 100:
                            logging.info(f"Using adjusted Reading value: {reading_value}°C -> {adjusted_temp}°C from {reading_name}")
                            return adjusted_temp
        
        # Method 3: Look for ambient/inlet temperature specifically
        if 'Temperatures' in data:
            ambient_sensors = ['Ambient', 'Inlet', 'System Board Inlet', 'Inlet Temp']
            for sensor in data['Temperatures']:
                sensor_name = sensor.get('Name', '')
                reading_c = sensor.get('ReadingCelsius')
                
                if any(ambient in sensor_name for ambient in ambient_sensors) and reading_c is not None:
                    adjusted_temp = reading_c - 60
                    if 0 <= adjusted_temp <= 100:
                        logging.info(f"Using adjusted ambient temperature: {reading_c}°C -> {adjusted_temp}°C from {sensor_name}")
                        return adjusted_temp
        
        logging.warning("No valid temperature found in Redfish data")
        return None

    def _try_legacy_api(self):
        """Try legacy iDRAC API endpoints"""
        endpoints = [
            "/data?get=tempReading,thermalReading",
            "/data?get=tempReading",
            "/data?get=thermalReading",
            "/sysmgmt/2012/server/temperature",
            "/data?get=sensorData"
        ]
        
        for endpoint in endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                response = self.session.get(url, auth=(self.username, self.password), timeout=10)
                
                if response.status_code == 200:
                    # Try JSON parsing
                    try:
                        data = response.json()
                        logging.info(f"Legacy API JSON response: {json.dumps(data)[:500]}...")
                        
                        if 'tempReading' in data:
                            return data['tempReading'], self._get_temperature_status(data['tempReading'])
                        if 'thermalReading' in data:
                            return data['thermalReading'], self._get_temperature_status(data['thermalReading'])
                    except:
                        # Try text parsing
                        text = response.text
                        logging.info(f"Legacy API text response: {text[:500]}...")
                        
                        # Look for temperature patterns
                        temp = self._extract_temperature_from_text(text)
                        if temp is not None:
                            return temp, self._get_temperature_status(temp)
                            
            except Exception as e:
                logging.warning(f"Legacy endpoint {endpoint} failed: {str(e)}")
                continue
                
        return None, "Legacy API unavailable"
    
    def _try_sensor_api(self):
        """Try sensor-specific endpoints"""
        endpoints = [
            "/sysmgmt/2015/bmc/info",
            "/sysmgmt/2012/server/info",
            "/data?get=ambientTemp",
            "/data?get=cpuTemp"
        ]
        
        for endpoint in endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                response = self.session.get(url, auth=(self.username, self.password), timeout=10)
                
                if response.status_code == 200:
                    text = response.text
                    temp = self._extract_temperature_from_text(text)
                    if temp is not None:
                        return temp, self._get_temperature_status(temp)
                        
            except Exception as e:
                continue
                
        return None, "Sensor API unavailable"
    
    def debug_redfish_response(self, endpoint):
        """Get full Redfish response for debugging"""
        try:
            url = f"{self.base_url}{endpoint}"
            response = self.session.get(url, auth=(self.username, self.password), timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                logging.info(f"=== FULL REDFISH RESPONSE FROM {endpoint} ===")
                logging.info(json.dumps(data, indent=2))
                logging.info("=== END REDFISH RESPONSE ===")
                return data
            else:
                logging.error(f"Redfish debug request failed with status: {response.status_code}")
        except Exception as e:
            logging.error(f"Redfish debug failed: {str(e)}")
    
    def _try_html_parsing(self):
        """Try parsing temperature from HTML pages"""
        endpoints = [
            "/",
            "/index.html", 
            "/main.html",
            "/restgui/start.html",
            "/sysmgmt/2012/server/dashboard"
        ]
        
        for endpoint in endpoints:
            try:
                url = f"{self.base_url}{endpoint}"
                response = self.session.get(url, auth=(self.username, self.password), timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Look for temperature in page text
                    text = soup.get_text()
                    temp = self._extract_temperature_from_text(text)
                    if temp is not None:
                        return temp, self._get_temperature_status(temp)
                        
            except Exception as e:
                continue
                
        return None, "HTML parsing failed"
    
    def _extract_temperature_from_text(self, text):
        """Extract temperature value from text using regex patterns"""
        patterns = [
            r'temp[^\d]*(\d+)°?c',
            r'temperature[^\d]*(\d+)°?c', 
            r'thermal[^\d]*(\d+)°?c',
            r'(\d+)°?c',
            r'(\d+)\s*degrees',
            r'ReadingCelsius[^\d]*(\d+)'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                for match in matches:
                    try:
                        temp = int(match)
                        if 0 <= temp <= 100:  # Reasonable temperature range
                            return temp
                    except ValueError:
                        continue
        return None
    
    def _get_temperature_status(self, temperature):
        """Determine temperature status"""
        if temperature >= Config.CRITICAL_THRESHOLD:
            return "CRITICAL"
        elif temperature >= Config.WARNING_THRESHOLD:
            return "WARNING"
        else:
            return "NORMAL"

class EmailSender:
    @staticmethod
    def send_email(temperature, status, is_test=False, email_type="status"):
        """Send email with temperature information"""
        try:
            if is_test:
                subject = "iDRAC Temperature Monitoring System"
                body = EmailSender._create_test_email_body()
            else:
                # Use test email format for all email types but include temperature data
                if email_type == "warning":
                    subject = f"WARNING: High Temperature Alert - {temperature}°C"
                elif email_type == "critical":
                    subject = f"CRITICAL: Immediate Action Required - {temperature}°C"
                elif email_type == "regular":
                    subject = f"Regular Temperature Report - {temperature}°C"
                else:
                    subject = f"Temperature Monitoring Report - {status}"
                
                body = EmailSender._create_full_report_body(temperature, status, email_type)
            
            # Create email manually
            message = f"Subject: {subject}\n\n{body}"
            
            # Send email
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.SENDER_EMAIL, Config.SENDER_PASSWORD)
                server.sendmail(Config.SENDER_EMAIL, Config.RECEIVER_EMAIL, message)
            
            logging.info(f"Email sent - Type: {email_type}, Temp: {temperature}°C, Status: {status}")
            return True
            
        except Exception as e:
            logging.error(f"Email sending failed: {str(e)}")
            return False
    
    @staticmethod
    def _create_full_report_body(temperature, status, email_type):
        """Create full report body using the test email format but with actual data"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if email_type == "warning":
            alert_note = "⚠️ WARNING: Temperature above normal threshold"
        elif email_type == "critical":
            alert_note = "🚨 CRITICAL: Temperature requires immediate attention"
        else:
            alert_note = "✅ System operating normally"
        
        # Use the test email format but with actual temperature data
        return f"""IDRAC Temperature Monitoring System

This automated report provides an overview of the current temperature of {Config.IDRAC_URL}.

{alert_note}

Status Overview:
Latest IDRAC Temperature: {temperature}°C
Latest Status: {status}

Threshold Information:
• Warning Level: {Config.WARNING_THRESHOLD}°C
• Critical Level: {Config.CRITICAL_THRESHOLD}°C

Monitoring Details:
Timestamp: {timestamp}
iDRAC URL SOURCE: {Config.IDRAC_URL}
Check Interval: {Config.CHECK_INTERVAL_MINUTES} minutes

Temperature History (Last 5 readings):
{EmailSender._get_temperature_history_text()}

System is operational and monitoring continues."""

    @staticmethod
    def _get_temperature_history_text():
        """Get formatted temperature history"""
        if not temperature_history:
            return "No history available"
        
        history_text = ""
        recent_history = temperature_history[-5:]  # Last 5 readings
        for entry in recent_history:
            timestamp = entry['timestamp'].strftime("%H:%M:%S")
            temp = entry['temperature']
            status = entry['status']
            history_text += f"• {timestamp}: {temp}°C ({status})\n"
        
        return history_text
    
    @staticmethod
    def _create_test_email_body():
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""IDRAC Temperature Monitoring System

This automated report provides an overview of the current temperature of  {Config.IDRAC_URL}.

Status Overview:
Latest IDRAC Temperature: {last_temperature}
Latest Status: {last_status}

Monitoring Details: 
Timestamp: {timestamp}
iDRAC URL SOURCE: {Config.IDRAC_URL}

System is operational."""

# Global instances
monitor = IDRACMonitor()
email_sender = EmailSender()

def should_send_email(status, email_type):
    """Determine if we should send an email"""
    global last_email_sent
    
    if email_type == "test":
        return True
    
    # Check configuration
    if status == "WARNING" and not Config.SEND_WARNING_EMAILS:
        return False
    if status == "CRITICAL" and not Config.SEND_CRITICAL_EMAILS:
        return False
    if email_type == "regular" and not Config.SEND_REGULAR_REPORTS:
        return False
    
    # Anti-spam cooldown
    now = datetime.now()
    last_sent = last_email_sent.get(email_type)
    
    if last_sent and (now - last_sent) < timedelta(minutes=30):
        return False
    
    last_email_sent[email_type] = now
    return True

def check_temperature_and_notify():
    """Check temperature and send notifications"""
    global last_temperature, last_status, temperature_history
    
    try:
        logging.info("Checking temperature...")
        temperature, status = monitor.get_temperature()
        
        if temperature is not None:
            last_temperature = temperature
            last_status = status
            
            # Store history
            temperature_history.append({
                'timestamp': datetime.now(),
                'temperature': temperature,
                'status': status
            })
            
            # Keep history manageable
            if len(temperature_history) > 100:
                temperature_history = temperature_history[-100:]
            
            logging.info(f"Temperature: {temperature}°C, Status: {status}")
            
            # Send appropriate emails using the new full report format
            if status == "CRITICAL" and should_send_email(status, "critical"):
                email_sender.send_email(temperature, status, email_type="critical")
                logging.info("Critical alert sent")
            
            elif status == "WARNING" and should_send_email(status, "warning"):
                email_sender.send_email(temperature, status, email_type="warning")
                logging.info("Warning alert sent")
            
            # Regular report every 60 minutes
            elif Config.SEND_REGULAR_REPORTS and should_send_email(status, "regular"):
                email_sender.send_email(temperature, status, email_type="regular")
                logging.info("Regular report sent")
            
            return temperature, status
        else:
            logging.warning("Could not retrieve temperature")
            return None, status
            
    except Exception as e:
        logging.error(f"Temperature check error: {str(e)}")
        return None, str(e)

def send_immediate_full_report():
    """Send an immediate full report using the test email format"""
    global last_temperature, last_status
    
    try:
        # Get current temperature first
        temperature, status = check_temperature_and_notify()
        
        if temperature is None:
            # Use last known values if current reading fails
            temperature = last_temperature
            status = last_status
        
        if temperature is not None:
            success = email_sender.send_email(temperature, status, email_type="regular")
            if success:
                logging.info("Immediate full report sent successfully")
                return True
            else:
                logging.error("Failed to send immediate full report")
                return False
        else:
            logging.error("No temperature data available for full report")
            return False
            
    except Exception as e:
        logging.error(f"Error sending immediate full report: {str(e)}")
        return False

def schedule_next_email(delay_minutes):
    """Schedule the next email to be sent after specified delay"""
    global scheduled_email_job, next_email_delay
    
    try:
        # Validate delay
        if delay_minutes < 5 or delay_minutes > 60:
            return False, "Delay must be between 5 and 60 minutes"
        
        # Remove existing scheduled job if any
        if scheduled_email_job:
            scheduler.remove_job(scheduled_email_job.id)
        
        # Schedule new job
        run_time = datetime.now() + timedelta(minutes=delay_minutes)
        job = scheduler.add_job(
            func=send_immediate_full_report,
            trigger="date",
            run_date=run_time,
            id=f'scheduled_email_{run_time.strftime("%Y%m%d%H%M%S")}'
        )
        
        scheduled_email_job = job
        next_email_delay = delay_minutes
        
        logging.info(f"Next email scheduled in {delay_minutes} minutes at {run_time}")
        return True, f"Next email scheduled in {delay_minutes} minutes"
        
    except Exception as e:
        logging.error(f"Error scheduling next email: {str(e)}")
        return False, str(e)

def scheduled_monitoring():
    """Scheduled monitoring task"""
    with app.app_context():
        check_temperature_and_notify()

# Initialize scheduler
scheduler = BackgroundScheduler()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/temperature', methods=['GET'])
def api_get_temperature():
    """API endpoint to get current temperature"""
    temperature, status = check_temperature_and_notify()
    
    return jsonify({
        'success': temperature is not None,
        'temperature': temperature,
        'status': status,
        'timestamp': datetime.now().isoformat(),
        'message': 'Temperature retrieved successfully' if temperature else status
    })

@app.route('/api/debug-redfish', methods=['GET'])
def api_debug_redfish():
    """Debug endpoint to see full Redfish response"""
    endpoint = request.args.get('endpoint', '/redfish/v1/Chassis/System.Embedded.1/Thermal')
    data = monitor.debug_redfish_response(endpoint)
    
    if data:
        return jsonify({
            'success': True,
            'endpoint': endpoint,
            'data': data
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Failed to get Redfish data'
        })

@app.route('/api/send-test-email', methods=['POST'])
def api_send_test_email():
    """API endpoint to send test email"""
    success = email_sender.send_email(None, None, is_test=True)
    return jsonify({
        'success': success,
        'message': 'Test email sent successfully' if success else 'Failed to send test email'
    })

@app.route('/api/send-full-report', methods=['POST'])
def api_send_full_report():
    """API endpoint to send immediate full report"""
    success = send_immediate_full_report()
    return jsonify({
        'success': success,
        'message': 'Full report sent successfully' if success else 'Failed to send full report'
    })

@app.route('/api/schedule-next-email', methods=['POST'])
def api_schedule_next_email():
    """API endpoint to schedule next email with custom delay"""
    data = request.get_json()
    
    if not data or 'delay_minutes' not in data:
        return jsonify({
            'success': False,
            'message': 'delay_minutes parameter is required'
        })
    
    delay_minutes = data['delay_minutes']
    
    try:
        delay_minutes = int(delay_minutes)
    except ValueError:
        return jsonify({
            'success': False,
            'message': 'delay_minutes must be a number'
        })
    
    success, message = schedule_next_email(delay_minutes)
    
    return jsonify({
        'success': success,
        'message': message
    })

@app.route('/api/schedule-status', methods=['GET'])
def api_schedule_status():
    """API endpoint to get scheduling status"""
    status = {
        'next_email_scheduled': scheduled_email_job is not None,
        'next_email_delay': next_email_delay,
        'next_run_time': scheduled_email_job.next_run_time.isoformat() if scheduled_email_job else None,
        'monitoring_interval': Config.CHECK_INTERVAL_MINUTES
    }
    
    return jsonify({
        'success': True,
        'status': status
    })

@app.route('/api/status', methods=['GET'])
def api_get_status():
    """API endpoint to get system status"""
    return jsonify({
        'last_temperature': last_temperature,
        'last_status': last_status,
        'last_check': temperature_history[-1]['timestamp'].isoformat() if temperature_history else None,
        'monitoring_active': scheduler.running,
        'check_interval': Config.CHECK_INTERVAL_MINUTES,
        'temperature_history_count': len(temperature_history)
    })

if __name__ == '__main__':
    setup_logging()
    
    logging.info("Starting iDRAC Temperature Monitor...")
    
    # Test initial connection
    logging.info("Testing iDRAC connection...")
    temp, status = monitor.get_temperature()
    
    if temp is not None:
        logging.info(f"Initial temperature reading: {temp}°C")
    else:
        logging.warning(f"Initial temperature reading failed: {status}")
    
    # Start scheduler
    scheduler.add_job(
        func=scheduled_monitoring,
        trigger="interval",
        minutes=Config.CHECK_INTERVAL_MINUTES,
        id='temperature_monitoring'
    )
    scheduler.start()
    logging.info(f"Scheduler started - checking every {Config.CHECK_INTERVAL_MINUTES} minutes")
    
    # Start Flask app
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)