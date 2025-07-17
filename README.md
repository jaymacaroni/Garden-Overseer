# GrowAGarden Overseer

This Discord bot automatically tracks item stock changes in the GrowAGarden game and notifies subscribed users when items become available. It features scheduled scraping, manual updates, and robust error handling.

## Features

- **Scheduled scraping** every 5 minutes at XX:01, XX:06, etc. (EST)
- **Item subscriptions** with fuzzy matching
- **Manual scraping** via `/scrape` command
- **Automatic retries** for failed scrapes (3 attempts)
- **Owner notifications** for critical failures
- **Detailed stock embeds** with visual formatting

## Installation

### Prerequisites
- Python 3.8+
- Discord bot token
- Windows Server 2022 (or compatible system)

### Setup Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/growagarden-bot.git
   cd growagarden-bot
   ```

2. **Create a virtual environment**
   ```cmd
   py -m venv bot-env
   bot-env\Scripts\activate
   ```

3. **Install dependencies**
   ```cmd
   pip install discord.py[voice] python-dotenv beautifulsoup4 aiohttp pytz lxml
   ```

4. **Create configuration file**
   - Create `code.env` in the project root with:
     ```env
     TOKEN=your_discord_bot_token
     ADMIN_ROLE_IDS=role_id1,role_id2
     OWNER_ID=your_discord_user_id
     ```

## Configuration

### Environment Variables
| Variable | Description | Example |
|----------|-------------|---------|
| `TOKEN` | Discord bot token | `MTEwMT...` |
| `ADMIN_ROLE_IDS` | Comma-separated role IDs for admin commands | `123456789,987654321` |
| `OWNER_ID` | Discord user ID for error notifications | `112233445566778899` |

### Channel Setup
Create a channel named `#growagarden` where the bot will post updates

## Usage

### Commands
| Command | Description | Example |
|---------|-------------|---------|
| `/scrape` | Manually fetch current stock | `/scrape` |
| `/autoscrape` | Enable/disable auto-scraping | `/autoscrape enable` |
| `/sub` | Subscribe to item alerts | `/sub golden seed, diamond hoe` |
| `/unsub` | Unsubscribe from items | `/unsub golden seed` |
| `/mylist` | List your subscriptions | `/mylist` |

### Subscription Examples
```
/sub golden seed, diamond hoe, ruby shovel
/unsub golden seed
/mylist
```

## Windows Server Deployment

### Execution Policy Setup
1. Open PowerShell as Administrator
2. Run:
   ```powershell
   Set-ExecutionPolicy RemoteSigned -Force
   ```

### Create Startup Script
Create `start_bot.bat` with:
```bat
@echo off
call bot-env\Scripts\activate
python bot.py
```

### Schedule as Task
1. Open Task Scheduler
2. Create Basic Task:
   - **Name**: GrowAGarden Bot
   - **Trigger**: At system startup
   - **Action**: Start a program
   - **Program**: `cmd.exe`
   - **Arguments**: `/c "C:\path\to\start_bot.bat"`
3. Check "Run with highest privileges"
4. Select "Run whether user is logged on or not"
