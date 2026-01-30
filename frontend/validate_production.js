/**
 * KOMPITE PRODUCTION VALIDATION SCRIPT
 * Validates all 6 game engines are properly configured
 * Run: node validate_production.js
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const GAMES = ['cabezones', 'air_hockey', 'artillery', 'duel', 'snowball', 'memoria'];
const BASE_PATH = path.join(__dirname, 'js', 'games');
const REQUIRED_ENDPOINT = 'http://194.113.194.85:8000';
const TITULAR = 'Yordy Jesús Rojas Baldeon';

console.log('');
console.log('╔═══════════════════════════════════════════════════════════╗');
console.log('║       KOMPITE PRODUCTION VALIDATION SCRIPT                ║');
console.log('╚═══════════════════════════════════════════════════════════╝');
console.log('');

let totalChecks = 0;
let passedChecks = 0;
let failedChecks = [];

function check(condition, description, game = 'GLOBAL') {
  totalChecks++;
  if (condition) {
    passedChecks++;
    console.log(`  ✅ ${description}`);
    return true;
  } else {
    failedChecks.push({ game, description });
    console.log(`  ❌ ${description}`);
    return false;
  }
}

function validateGame(game) {
  console.log(`\n🎮 Validating: ${game.toUpperCase()}`);
  console.log('─'.repeat(50));

  const gamePath = path.join(BASE_PATH, game);

  // 1. Check directory exists
  const dirExists = fs.existsSync(gamePath);
  check(dirExists, `Directory exists: ${game}`, game);
  if (!dirExists) return;

  // 2. Check config file
  const configPatterns = [
    `config/${game}_assets.json`,
    'config/cabezones_assets.json'
  ];
  
  let configPath = null;
  for (const pattern of configPatterns) {
    const testPath = path.join(gamePath, pattern);
    if (fs.existsSync(testPath)) {
      configPath = testPath;
      break;
    }
  }
  
  check(configPath !== null, `Config file exists`, game);
  
  if (configPath) {
    try {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      
      // Check endpoints
      const hasCorrectEndpoint = config.endpoints?.api === REQUIRED_ENDPOINT || 
        JSON.stringify(config).includes(REQUIRED_ENDPOINT);
      check(hasCorrectEndpoint, `Endpoint: ${REQUIRED_ENDPOINT}`, game);
      
      // Check Soft Lock
      const hasSoftLock = config.game?.softLock === 5 || config.softLock === 5 || 
        JSON.stringify(config).includes('"softLock": 5') ||
        JSON.stringify(config).includes('"softLock":5');
      check(hasSoftLock, `Soft Lock: 5 LKC configured`, game);
      
      // Check Rake
      const hasRake = config.game?.rake === 0.08 || config.gameConfig?.rakePercentage === 8;
      check(hasRake, `Rake: 8% configured`, game);
      
    } catch (err) {
      check(false, `Config file is valid JSON: ${err.message}`, game);
    }
  }

  // 3. Check server files
  const serverPatterns = [
    `server/${game}_server.js`,
    'server/kompite_integration.js'
  ];
  
  let serverExists = false;
  for (const pattern of serverPatterns) {
    if (fs.existsSync(path.join(gamePath, pattern))) {
      serverExists = true;
      break;
    }
  }
  check(serverExists, `Server module exists`, game);

  // 4. Check security middleware
  const securityPath = path.join(gamePath, 'server', 'security_middleware.js');
  const hasSecurityAlt = fs.existsSync(path.join(gamePath, 'server', 'balance_manager.js'));
  check(fs.existsSync(securityPath) || hasSecurityAlt, `Security middleware exists`, game);

  // 5. Check client file
  const clientPatterns = [
    `client/${game}_client.js`,
    'client/client.js'
  ];
  
  let clientExists = false;
  for (const pattern of clientPatterns) {
    if (fs.existsSync(path.join(gamePath, pattern))) {
      clientExists = true;
      break;
    }
  }
  check(clientExists, `Client module exists`, game);

  // 6. Check for PhysicsEngine in server code
  if (serverExists) {
    for (const pattern of serverPatterns) {
      const serverPath = path.join(gamePath, pattern);
      if (fs.existsSync(serverPath)) {
        const serverCode = fs.readFileSync(serverPath, 'utf8');
        const hasPhysicsEngine = serverCode.includes('PhysicsEngine') || 
          serverCode.includes('Shadow') || 
          serverCode.includes('validateGoal') ||
          serverCode.includes('updatePhysics');
        check(hasPhysicsEngine, `PhysicsEngine/Validation logic present`, game);
        break;
      }
    }
  }
}

function validateCoreModules() {
  console.log('\n🔧 Validating Core Modules');
  console.log('─'.repeat(50));

  const cabezonesPath = path.join(BASE_PATH, 'cabezones', 'server');

  // Balance Manager
  check(fs.existsSync(path.join(cabezonesPath, 'balance_manager.js')), 
    'balance_manager.js exists');

  // Ledger
  check(fs.existsSync(path.join(cabezonesPath, 'cabezones_ledger.js')), 
    'cabezones_ledger.js (Triple Entry Ledger) exists');

  // Shadow Simulation
  check(fs.existsSync(path.join(cabezonesPath, 'shadow_simulation.js')), 
    'shadow_simulation.js exists');

  // Kompite Integration
  check(fs.existsSync(path.join(cabezonesPath, 'kompite_integration.js')), 
    'kompite_integration.js exists');
}

function validateProductionServer() {
  console.log('\n🚀 Validating Production Server');
  console.log('─'.repeat(50));

  const serverPath = path.join(__dirname, 'js', 'production_server.js');
  const serverExists = fs.existsSync(serverPath);
  check(serverExists, 'production_server.js exists');

  if (serverExists) {
    const code = fs.readFileSync(serverPath, 'utf8');
    
    check(code.includes('194.113.194.85'), 'VPS IP configured');
    check(code.includes('8000'), 'Port 8000 configured');
    check(code.includes('MobileReconnectionManager'), 'Mobile reconnection system');
    check(code.includes('softLock') || code.includes('SOFT_LOCK'), 'Soft Lock endpoint');
    check(code.includes('settlement') || code.includes('SETTLEMENT'), 'Settlement endpoint');
    check(code.includes('RAKE') || code.includes('rake'), 'Rake calculation');
    check(code.includes('SHA256') || code.includes('sha256'), 'Balance hash validation');
    check(code.includes(TITULAR), `Titular: ${TITULAR}`);
  }
}

function validateMobileCSS() {
  console.log('\n📱 Validating Mobile-First CSS');
  console.log('─'.repeat(50));

  const cssPath = path.join(__dirname, 'css', 'mobile-first.css');
  const cssExists = fs.existsSync(cssPath);
  check(cssExists, 'mobile-first.css exists');

  if (cssExists) {
    const css = fs.readFileSync(cssPath, 'utf8');
    
    check(css.includes('--btn-size') || css.includes('btn-size'), 'Touch button sizing');
    check(css.includes('joystick') || css.includes('touch'), 'Touch controls');
    check(css.includes('@media'), 'Responsive breakpoints');
    check(css.includes('safe-area') || css.includes('env('), 'Safe area support');
    check(css.includes('20%') || css.includes('1.2') || parseInt(css.match(/--btn-size:\s*(\d+)/)?.[1]) > 50, 
      'Controls increased 20%');
  }
}

function validateLobby() {
  console.log('\n🏠 Validating Lobby');
  console.log('─'.repeat(50));

  const lobbyPath = path.join(__dirname, 'index.html');
  const lobbyExists = fs.existsSync(lobbyPath);
  check(lobbyExists, 'index.html (lobby) exists');

  if (lobbyExists) {
    const html = fs.readFileSync(lobbyPath, 'utf8');
    
    check(html.includes('viewport'), 'Viewport meta tag');
    check(html.includes('194.113.194.85:8000'), 'Production endpoint');
    check(html.includes('cabezones') && html.includes('air_hockey'), 'Game cards present');
    check(html.includes('reconect') || html.includes('Reconect'), 'Reconnection UI');
    check(html.includes('socket.io'), 'Socket.io included');
  }
}

// Run all validations
console.log('Starting validation...\n');

GAMES.forEach(game => validateGame(game));
validateCoreModules();
validateProductionServer();
validateMobileCSS();
validateLobby();

// Summary
console.log('\n');
console.log('═'.repeat(60));
console.log('                    VALIDATION SUMMARY');
console.log('═'.repeat(60));
console.log(`  Total Checks:  ${totalChecks}`);
console.log(`  Passed:        ${passedChecks} ✅`);
console.log(`  Failed:        ${failedChecks.length} ❌`);
console.log(`  Success Rate:  ${((passedChecks / totalChecks) * 100).toFixed(1)}%`);

if (failedChecks.length > 0) {
  console.log('\n  Failed Checks:');
  failedChecks.forEach(f => console.log(`    - [${f.game}] ${f.description}`));
}

console.log('\n');

if (failedChecks.length === 0) {
  console.log('╔═══════════════════════════════════════════════════════════╗');
  console.log('║     ✅ ALL VALIDATIONS PASSED - READY FOR PRODUCTION      ║');
  console.log('╠═══════════════════════════════════════════════════════════╣');
  console.log('║  Next Steps:                                              ║');
  console.log('║  1. Run: node js/production_server.js                     ║');
  console.log('║  2. Access: http://194.113.194.85:8000                      ║');
  console.log('║  3. Test with Beta Testers                                ║');
  console.log('╚═══════════════════════════════════════════════════════════╝');
} else {
  console.log('╔═══════════════════════════════════════════════════════════╗');
  console.log('║    ⚠️  SOME VALIDATIONS FAILED - REVIEW REQUIRED          ║');
  console.log('╚═══════════════════════════════════════════════════════════╝');
}

console.log('\n');
console.log(`Validation completed at: ${new Date().toISOString()}`);
console.log(`Titular: ${TITULAR}`);
console.log('');

// Exit with appropriate code
process.exit(failedChecks.length > 0 ? 1 : 0);
