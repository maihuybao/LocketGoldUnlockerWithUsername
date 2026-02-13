# Locket Gold Username Unlock

Unlock Locket Gold premium features **with just a username** — no password required! Built with Flask, featuring an intelligent queue management system and beautiful glassmorphism UI.

![Locket Gold](https://img.shields.io/badge/Locket-Gold-FFD700?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.0+-000000?style=for-the-badge&logo=flask&logoColor=white)

## Features

### Core Features

- **🔑 Username-Only Unlock**: No password needed — just enter the Locket username to unlock Gold
- **👤 User Verification**: Preview user profile and information before processing
- **📱 Real-time Notifications**: Telegram integration for instant success alerts
- **🎓 Educational Purpose**: Learn about API interactions and modern web development

### Queue Management System

- **Smart Queue Processing**: Handles multiple concurrent requests sequentially
- **Real-time Position Updates**: See your exact position in queue
- **Wait Time Estimation**: Dynamic countdown based on actual processing times
- **Progress Visualization**: Animated progress bar showing queue advancement
- **Total Queue Display**: Know exactly how many people are waiting

### User Interface

- **Modern Glassmorphism Design**: Beautiful frosted glass effects with gradient accents
- **Responsive Layout**: Works perfectly on all screen sizes
- **Smooth Animations**: Polished micro-interactions and transitions
- **Real-time Countdown**: Live timer that ticks down every second
- **Status Indicators**: Clear visual feedback for all states (waiting, processing, completed)

## Screenshots

### Main Interface

Beautiful glassmorphism design with gradient backgrounds and smooth animations.

### Queue Status Modal

Real-time queue position updates with countdown timer and progress bar.

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Setup

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd LocketGoldUsername
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   Create a `.env` file in the project root:

   ```env
   EMAIL=your_locket_email@example.com
   PASSWORD=your_locket_password
   ```

4. **Run the application**

   ```bash
   python app.py
   ```

5. **Access the web interface**

   Open your browser and navigate to:

   ```
   http://localhost:5000
   ```

## Usage

### Basic Usage

1. **Enter Username**: Type the Locket username you want to unlock Gold for
2. **Verify User**: Click "Check User Info" to preview the account details
3. **Confirm**: Review the information and click "Continue"
4. **Wait in Queue**: Watch the real-time queue status with countdown timer
5. **Success**: Receive confirmation when Gold is unlocked

### Queue System

When multiple users submit requests simultaneously:

- Each request receives a unique position in the queue
- Requests are processed one at a time (sequential processing)
- Real-time updates show your position, total waiting, and estimated time
- Countdown timer ticks down every second for better UX
- Progress bar visually represents queue advancement

### Configuration Profile (iOS)

After unlocking Gold, install the configuration profile to prevent revocation:

1. Click "Download Configuration Profile" button
2. Install the profile on your iOS device
3. Restart the Locket app

## Project Structure

```
LocketGoldUsername/
├── app.py                  # Main Flask application with queue manager
├── auth.py                 # Locket authentication handler
├── api.py                  # Locket API wrapper
├── requirements.txt        # Python dependencies
├── .env                    # Environment configuration (not in repo)
├── templates/
│   └── index.html         # Frontend with queue UI
└── static/
    └── locket.mobileconfig # iOS configuration profile
```

## How It Works: Restore Purchase Mechanism

This tool unlocks Locket Gold by exploiting the **App Store sandbox restore purchase API**. Here's the technical breakdown:

### The Restore Purchase Flow

1. **User Lookup**
   - Enter target username (e.g., `@john_doe`)
   - Tool queries Locket API to fetch user's unique ID (UID)
   - Displays user profile for verification

2. **Restore Purchase Request**
   - Sends a restore purchase request to Locket's backend API
   - Request mimics the official Locket app's restore purchase call
   - Uses App Store sandbox credentials (no actual payment)

3. **Sandbox Entitlement Grant**
   - Locket's server validates the restore request against App Store's sandbox environment
   - Sandbox environment treats the request as a valid purchase restoration
   - Server grants `Gold` entitlement to the target user's account

4. **Instant Activation**
   - Gold subscription becomes active immediately
   - User sees premium features unlocked in their Locket app
   - No actual App Store purchase or payment involved

### Technical Mechanism

**Why Username Only?**

- Locket's API allows restore purchase requests with just the user's UID
- No password authentication required for restore operations
- The tool acts as a middleman between you and Locket's API

**App Store Sandbox**

- App Store has two environments: **Production** and **Sandbox**
- Sandbox is for testing in-app purchases without real money
- Locket's restore purchase endpoint accepts sandbox credentials
- This allows "restoring" purchases that were never actually made

**API Call Flow**

```
User Input (username)
  → Locket API: getUserByUsername()
    → Returns user UID
      → Locket API: restorePurchase(UID)
        → App Store Sandbox Validation
          → Grant Gold Entitlement
            → Success!
```

### Why It Works

1. **Sandbox vs Production**: Locket's API doesn't strictly validate whether the restore request is from sandbox or production environment
2. **No Receipt Validation**: The restore endpoint doesn't verify actual purchase receipts
3. **UID-based Grants**: Entitlements are tied to UID, which is publicly accessible via username lookup

### Limitations

⚠️ **Temporary Unlock**:

- Gold is active only until you **log out** of Locket
- Logging out clears the local entitlement cache
- Must re-unlock after each logout

⚠️ **Revocation Protection**:

- Install the configuration profile (`.mobileconfig`) to block Locket's revocation servers
- Without the profile, Locket may revoke Gold after detecting the sandbox grant
- Profile blocks network requests to revocation endpoints

## Technical Details

### Backend Architecture

**Queue Manager**:

- Thread-safe queue using Python's `queue.Queue` and `threading.Lock`
- Background worker thread for sequential request processing
- Client tracking with UUID-based identifiers
- Processing time history for accurate wait time estimation

**API Endpoints**:

- `POST /api/get-user-info`: Fetch user details by username
- `POST /api/restore`: Add request to queue and return client_id
- `POST /api/queue/status`: Poll for current queue position and status

### Frontend Features

**Real-time Updates**:

- Polls server every 1 second for queue status
- Independent countdown timer for smooth UX
- Automatic cleanup on completion or error

**Visual Feedback**:

- Position indicator (e.g., "#3" or "Processing")
- Total waiting count
- Estimated time with countdown (e.g., "12s" or "1m 30s")
- Animated progress bar (0-100%)

### Security Notes

- Credentials stored in `.env` file (excluded from version control)
- API tokens refreshed automatically on expiration
- All API communications use HTTPS
- No sensitive data logged or stored

## Queue System Details

### How It Works

1. **Request Submission**: User clicks "Continue" → joins queue
2. **Queue Assignment**: Receives unique `client_id` and initial position
3. **Background Processing**: Worker thread processes requests sequentially
4. **Status Updates**: Client polls every 1s for position/time updates
5. **Countdown**: Timer ticks down independently for smooth visualization
6. **Completion**: Shows success/error message when processing finishes

### Wait Time Calculation

- Tracks actual processing time for each request
- Uses moving average of last 10 completions
- Formula: `position × average_processing_time`
- Defaults to 5 seconds per request if no history

### Performance

- **Throughput**: ~1 request per 5 seconds (API-limited)
- **Concurrency**: Sequential processing (prevents API rate limits)
- **Scalability**: Handles unlimited queue size (memory-limited)
- **Reliability**: Auto-retry on connection errors

## Configuration

### Environment Variables

| Variable   | Description             | Required |
| ---------- | ----------------------- | -------- |
| `EMAIL`    | Locket account email    | Yes      |
| `PASSWORD` | Locket account password | Yes      |

### Telegram Notifications

To enable Telegram notifications, update these values in `app.py`:

```python
bot_token = "YOUR_TELEGRAM_BOT_TOKEN"
chat_id = "YOUR_TELEGRAM_CHAT_ID"
```

## Troubleshooting

### Port Already in Use

```bash
# Kill process on port 5000
lsof -ti:5000 | xargs kill -9

# Or change port in app.py
app.run(debug=True, port=8000)  # Use different port
```

### Authentication Failed

- Verify `.env` file exists with correct credentials
- Check if Locket account credentials are valid
- Ensure email and password have no extra spaces

### Queue Not Processing

- Check terminal logs for errors
- Verify API credentials are correct
- Ensure internet connection is stable

## Disclaimer

⚠️ **Educational Purpose Only**

This project is created for **educational purposes only** to demonstrate:

- Web application development with Flask
- Queue management systems
- Real-time status updates
- Modern UI/UX design patterns
- API integration techniques

**Important Notes**:

- This tool is for **iOS devices only**
- Gold subscription is valid **only until you log out** of the Locket app
- Use responsibly and in compliance with Locket's Terms of Service
- The developers are not responsible for any misuse of this tool

## Credits

- **Developer**: [Mai Huy Bao](https://maihuybao.dev)
- **Design**: Modern glassmorphism with gradient accents
- **Font**: [Outfit](https://fonts.google.com/specimen/Outfit) by Google Fonts
- **Icons**: SVG icons from various sources

## License

This project is provided as-is for educational purposes. Use at your own discretion.

---

Made with ❤️ by [Mai Huy Bao](https://maihuybao.dev)
