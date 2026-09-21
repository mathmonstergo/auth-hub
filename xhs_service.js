const { chromium } = require('/opt/auth-hub/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const RUNTIME_DIR = '/run/auth-hub';
const STATE_FILE = path.join(RUNTIME_DIR, 'xhs_login_state.json');
const SMS_INPUT_FILE = path.join(RUNTIME_DIR, 'xhs_sms_code.txt');
const ACTION_FILE = path.join(RUNTIME_DIR, 'xhs_action.json');
const LIVE_SHOT = path.join(RUNTIME_DIR, 'xhs_live.png');
const LOG_FILE = '/var/log/auth-hub/xhs.log';
const SHOT_DIR = '/opt/auth-hub/shots';
const HERMES_DIR = '/home/ubuntu/.hermes';
const XHS_COOKIE_PATH = path.join(HERMES_DIR, 'xhs_cookie.json');
const XHS_META_PATH = '/opt/auth-hub/xhs_meta.json';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36';

const QR_SELECTOR = 'img.qrcode-img';

// 登录页「手机号登录」表单里的验证码框：一直是可见的，绝不能当短信环节证据。
const PHONE_FORM_CODE_SELS = ['label.auth-code > input', 'input[placeholder*="验证码"]', 'input[placeholder*="短信"]'];

// 小红书短信风控弹窗（capptch-modal）。弹窗会跟随浏览器 locale 出中/英文，
// 实测英文环境文案：SMS Verification / Please enter verification code / Resend code(60s) / Verify
const SMS_MODAL_ROOT_SELS = [
    '[class*="capptch-modal"]',
    '[class*="captcha-modal"]',
    '[class*="modal-content-inner"]',
    '[class*="receive-number"]'
];
const SMS_MODAL_INPUT_SELS = [
    '[class*="capptch-modal"] input.r-input-inner',
    '[class*="capptch-modal"] input',
    '[class*="modal-content"] input[maxlength="6"]',
    'input.r-input-inner[maxlength="6"]',
    'input[placeholder*="verification code" i]',
    'input[placeholder*="请输入验证码"]',
    'input[autocomplete="one-time-code"]'
];
const SMS_MODAL_VERIFY_SELS = [
    '[class*="capptch-modal"] div[role="button"].btn-block',
    '[class*="capptch-modal"] div[role="button"]',
    '[class*="capptch-modal"] button',
    'div[role="button"]:has-text("Verify")',
    'button:has-text("Verify")',
    'div[role="button"]:has-text("验证")'
];
const SMS_PANEL_TEXT_RE = new RegExp([
    // 中文
    '为保障', '安全验证', '短信验证', '验证码已发送', '短信已发送', '请完成验证',
    '1\\d{2}\\*{2,}\\d{2,4}',
    // 英文（locale=en-US 时小红书会出英文弹窗）
    'SMS Verification', 'Verification code has been sent', 'Please enter verification code',
    'Resend code', "Didn't receive code", 'Verification code sent', 'Verify'
].join('|'), 'i');
const SMS_ERROR_TEXT_RE = /验证码错误|验证码不正确|验证码无效|验证码已过期|已失效|请重新获取|请重新输入|code is invalid|incorrect|invalid code|expired|try again/i;
const SEND_CODE_TEXTS = ['获取验证码', '发送验证码', '重新获取', '重新发送', '点击获取', 'Resend code', 'Resend'];

const sleep = ms => new Promise(r => setTimeout(r, ms));

function log(...args) {
    const line = `[${new Date().toISOString()}] ` + args.map(a => {
        if (typeof a === 'string') return a;
        try { return JSON.stringify(a); } catch (e) { return String(a); }
    }).join(' ') + '\n';
    try { fs.appendFileSync(LOG_FILE, line, 'utf-8'); } catch (e) {}
}

function setState(state) {
    try {
        fs.mkdirSync(RUNTIME_DIR, { recursive: true, mode: 0o700 });
        const tmp = STATE_FILE + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(state, null, 2), { encoding: 'utf-8', mode: 0o600 });
        fs.renameSync(tmp, STATE_FILE);
    } catch (e) { log('setState failed:', e.message); }
}

async function firstVisible(page, selectors) {
    for (const sel of selectors) {
        let els = [];
        try { els = await page.$$(sel); } catch (e) { continue; }
        for (const el of els) {
            try { if (await el.isVisible()) return el; } catch (e) {}
        }
    }
    return null;
}

async function pageText(page, limit) {
    try { return (await page.evaluate(() => document.body ? document.body.innerText : '')).replace(/\s+/g, ' ').slice(0, limit || 400); }
    catch (e) { return ''; }
}

async function screenshot(page, tag, dest) {
    try {
        const p = dest || path.join(SHOT_DIR, `xhs-${tag}-${Date.now()}.png`);
        if (!dest) fs.mkdirSync(SHOT_DIR, { recursive: true, mode: 0o755 });
        await page.screenshot({ path: p });
        return p;
    } catch (e) { log('screenshot failed:', e.message); return ''; }
}

async function pumpLiveShot(page) {
    try {
        await page.screenshot({ path: LIVE_SHOT });
        fs.chmodSync(LIVE_SHOT, 0o600);
    } catch (e) { log('live shot failed:', e.message); }
}

async function dumpPanel(page, tag) {
    try {
        const txt = await pageText(page, 3000);
        const html = (await page.content()).slice(0, 400000);
        fs.mkdirSync(SHOT_DIR, { recursive: true, mode: 0o755 });
        const p = path.join(SHOT_DIR, `xhs-dom-${tag}-${Date.now()}.txt`);
        fs.writeFileSync(p, `URL: ${page.url()}\n\n=== INNER TEXT ===\n${txt}\n\n=== HTML ===\n${html}\n`, 'utf-8');
        log('dom dump ->', p);
        return p;
    } catch (e) { log('dumpPanel failed:', e.message); return ''; }
}

// ---------- 短信弹窗识别 ----------

async function smsModalEl(page) {
    for (const sel of SMS_MODAL_ROOT_SELS) {
        let els = [];
        try { els = await page.$$(sel); } catch (e) { continue; }
        for (const el of els) {
            try {
                if (!(await el.isVisible())) continue;
                const t = await el.innerText().catch(() => '');
                if (t && SMS_PANEL_TEXT_RE.test(t)) return el;
            } catch (e) {}
        }
    }
    return null;
}

async function smsModalVisible(page) {
    return !!(await smsModalEl(page));
}

async function smsModalInfo(page) {
    const box = await smsModalEl(page);
    if (!box) return { present: false, text: '', phone: '' };
    const text = ((await box.innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
    const m = text.match(/\+?\d{1,3}\s*1\d{2}\*{2,}\d{2,4}/);
    return { present: true, text, phone: m ? m[0] : '' };
}

// 找验证码输入框：弹窗内在弹窗里找；弹窗已出现却找不到输入框时，
// 绝不回退到登录页手机表单（那会把验证码写错框，就是之前的卡住原因）。
async function findSmsCodeInput(page) {
    const modalBox = await smsModalEl(page);
    if (modalBox) {
        for (const sel of SMS_MODAL_INPUT_SELS) {
            let els = [];
            try { els = await modalBox.$$(sel); } catch (e) { continue; }
            for (const el of els) {
                try { if (await el.isVisible()) return { el, scoped: true }; } catch (e) {}
            }
        }
        // 兜底：弹窗内任意可见 input
        try {
            for (const el of await modalBox.$$('input')) {
                if (await el.isVisible()) return { el, scoped: true };
            }
        } catch (e) {}
        return { el: null, scoped: true };
    }
    const el = await firstVisible(page, PHONE_FORM_CODE_SELS);
    return { el, scoped: false };
}

// 点「Verify / 验证」按钮；等它从 disabled 变可点（输入 6 位后 Vue 才解禁）
async function clickVerify(page, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 8000);
    while (Date.now() < deadline) {
        const btn = await firstVisible(page, SMS_MODAL_VERIFY_SELS);
        if (btn) {
            let disabled = true;
            try {
                const cls = (await btn.getAttribute('class')) || '';
                const dis = await btn.getAttribute('disabled');
                disabled = cls.includes('disabled') || (dis !== null && dis !== undefined);
            } catch (e) {}
            if (!disabled) {
                try { await btn.click({ timeout: 3000 }); log('clickVerify: 已点击 Verify/验证'); return true; }
                catch (e) { log('clickVerify 点击失败:', e.message); }
            }
        }
        await sleep(300);
    }
    try { await page.keyboard.press('Enter'); log('clickVerify: 按钮一直禁用，改按 Enter'); return true; } catch (e) {}
    return false;
}

// 输入验证码：用键盘逐字敲，确保 Vue 的响应式校验生效（fill 直写 value 有时不触发 disabled 解锁）
async function typeSmsCode(page, inputEl, code) {
    try { await inputEl.click({ timeout: 3000 }); } catch (e) {}
    try { await inputEl.fill('', { timeout: 2000 }); } catch (e) {}
    let typed = false;
    try { await page.keyboard.type(code, { delay: 90 }); typed = true; }
    catch (e) { log('keyboard.type 失败:', e.message); }
    if (!typed) {
        try { await inputEl.fill(code); typed = true; } catch (e) { log('fill 兜底失败:', e.message); }
    }
    await sleep(400);
    let val = '';
    try { val = await inputEl.inputValue(); } catch (e) {}
    const pre = await smsModalInfo(page);
    log(`sms 提交：已写入 len=${(val || '').length}，弹窗文案="${pre.text.slice(0, 120)}"`);
    return { value: val, modalText: pre.text };
}

// 点「获取验证码 / Resend code」。防御：登录页手机表单里的同名按钮在手机号为空时必须跳过。
async function clickSendCode(page, requireFilledPhone) {
    // 1) 优先：短信弹窗内的重发入口（span.sms-code-get-text-counting = "Resend code(60s)"）
    const modalEl = await smsModalEl(page);
    if (modalEl) {
        const cand = await firstVisible(page, [
            '[class*="capptch-modal"] span.sms-code-get-text-counting',
            '[class*="capptch-modal"] [class*="resend"]',
            '[class*="capptch-modal"] [class*="get-text"]'
        ]) || await firstVisible(modalEl, ['span.sms-code-get-text-counting', '[class*="resend"]', '[class*="get-text"]']);
        if (cand) {
            const t = ((await cand.innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
            const cls = (await cand.getAttribute('class')) || '';
            const counting = /\(\s*\d+\s*s\s*\)/i.test(t) || /count/i.test(cls);
            if (counting) {
                log('send-code: 弹窗内重发倒计时中，跳过 ->', t);
            } else {
                try { await cand.click({ timeout: 3000 }); log('clickSendCode: 已点击弹窗内「' + t + '」'); return true; }
                catch (e) { log('clickSendCode: 弹窗内重发点击失败:', e.message); }
            }
        }
    }
    // 2) 页面通用按钮（保留原有的手机号防空点防御）
    for (const t of SEND_CODE_TEXTS) {
        let loc;
        try { loc = page.locator(`text="${t}"`); } catch (e) { continue; }
        let n = 0;
        try { n = await loc.count(); } catch (e) { n = 0; }
        for (let i = 0; i < n; i++) {
            const c = loc.nth(i);
            try {
                if (!(await c.isVisible())) continue;
                const meta = await c.evaluate(el => {
                    const box = el.closest('form, [role="dialog"], [class*="modal"], [class*="container"]') || el.parentElement;
                    const inputs = box ? Array.from(box.querySelectorAll('input')) : [];
                    const phone = inputs.find(x => /手机|phone/i.test(x.placeholder || '') || x.type === 'tel');
                    const code = inputs.find(x => /验证码|短信|verification/i.test(x.placeholder || ''));
                    const pagePhones = Array.from(document.querySelectorAll('input'))
                        .filter(x => /手机|phone/i.test(x.placeholder || '') || x.type === 'tel');
                    const boxText = ((box && box.innerText) || '').replace(/\s+/g, ' ').slice(0, 80);
                    return {
                        phoneValue: phone ? phone.value : null,
                        hasPhoneInput: !!phone,
                        hasCodeInput: !!code,
                        inModal: /SMS Verification|Verification code|验证码已发送|短信验证/i.test(boxText),
                        pagePhoneFilled: pagePhones.some(x => (x.value || '').replace(/\D/g, '').length >= 6),
                        boxText
                    };
                });
                log('send-code candidate:', t, '#' + i, meta);
                if (requireFilledPhone && !meta.inModal) {
                    const digits = (meta.phoneValue || '').replace(/\D/g, '');
                    if (digits.length < 6 && !meta.pagePhoneFilled) {
                        log('send-code: 跳过（未扫码/手机号为空，属登录页通用表单，点击会触发风控）');
                        continue;
                    }
                }
                const aria = await c.getAttribute('aria-disabled');
                const cls = (await c.getAttribute('class')) || '';
                if (aria === 'true' || (cls.includes('disabled') && !meta.inModal)) {
                    log('send-code: 按钮处于禁用态，跳过');
                    continue;
                }
                await c.click({ timeout: 3000 });
                log(`clickSendCode: 已点击 "${t}"`);
                return true;
            } catch (e) {
                log(`clickSendCode: "${t}" #${i} 点击失败: ${e.message}`);
            }
        }
    }
    log('clickSendCode: 未找到可点击的「获取验证码」按钮');
    return false;
}

async function hasCaptcha(page) {
    for (const p of ['.captcha', '.slider', '.nc_container', 'iframe[src*="captcha"]']) {
        try { if (await page.isVisible(p, { timeout: 200 })) return p; } catch (e) {}
    }
    try {
        const t = await pageText(page, 4000);
        if (/滑动|拖动|向右滑|完成拼图|安全验证|slide to verify/i.test(t)) return 'text-risk-control';
    } catch (e) {}
    return '';
}

async function fetchProfile(cookieStr) {
    if (typeof fetch !== 'function') return {};
    try {
        const res = await fetch('https://edith.xiaohongshu.com/api/sns/web/v2/user/me', {
            headers: { 'Cookie': cookieStr, 'User-Agent': UA, 'Referer': 'https://www.xiaohongshu.com/' },
            signal: AbortSignal.timeout(8000)
        });
        const j = await res.json();
        const d = j && j.data;
        if (d) return { nickname: d.nickname || d.nick_name || '', user_id: d.user_id || d.userId || '' };
    } catch (e) { log('fetchProfile failed:', e.message); }
    return {};
}

async function runLogin() {
    log('=== xhs login run start (node ' + process.version + ') ===');
    try { if (fs.existsSync(SMS_INPUT_FILE)) fs.unlinkSync(SMS_INPUT_FILE); } catch (e) {}
    try { if (fs.existsSync(ACTION_FILE)) fs.unlinkSync(ACTION_FILE); } catch (e) {}
    try { if (fs.existsSync(LIVE_SHOT)) fs.unlinkSync(LIVE_SHOT); } catch (e) {}

    setState({ polling_state: 'waiting', msg: '正在调起小红书官方通道并生成二维码...', qr_b64: '', started_at: Date.now() });

    let browser = null;
    try {
        browser = await chromium.launch({
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        });
        // locale/timezone 走中国区，让风控弹窗出中文；英文文案的匹配逻辑同样保留，双向兜底。
        const context = await browser.newContext({
            userAgent: UA,
            locale: 'zh-CN',
            timezoneId: 'Asia/Shanghai',
            extraHTTPHeaders: { 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' }
        });
        const page = await context.newPage();

        await page.goto('https://www.xiaohongshu.com/explore', { waitUntil: 'networkidle', timeout: 35000 });

        const el = await page.waitForSelector(QR_SELECTOR, { timeout: 20000 });
        const qrB64 = await el.getAttribute('src');
        if (!qrB64 || !qrB64.startsWith('data:image')) throw new Error('未获取到有效的二维码图片');
        log('qr generated, len=', qrB64.length, 'url=', page.url());
        await pumpLiveShot(page);

        const initialCookies = await context.cookies();
        const initialCookieDict = {};
        initialCookies.forEach(c => initialCookieDict[c.name] = c.value);
        const unloggedSession = initialCookieDict['web_session'] || '';

        setState({
            polling_state: 'waiting',
            msg: '请使用手机【小红书 App】首页「扫一扫」并确认授权...',
            qr_b64: qrB64,
            started_at: Date.now()
        });

        const startTime = Date.now();
        const timeoutMs = 420000;
        let smsPanelSeen = false;
        let smsClicks = 0;
        let lastSmsClickAt = 0;
        let lastSubmitAt = 0;
        let submitAttempts = 0;
        let captchaWarned = false;
        let lastShotAt = Date.now();
        let lastBeatAt = 0;

        while (Date.now() - startTime < timeoutMs) {
            await sleep(1000);

            let qrVisible = false;
            try { qrVisible = await page.isVisible(QR_SELECTOR, { timeout: 300 }); } catch (e) { qrVisible = false; }

            const modalInfo = await smsModalInfo(page);
            const modalSms = modalInfo.present;
            const visibleCodeInput = await firstVisible(page, PHONE_FORM_CODE_SELS);
            const smsPhase = modalSms || (!!visibleCodeInput && !qrVisible);

            if (Date.now() - lastShotAt > 8000) {
                lastShotAt = Date.now();
                await pumpLiveShot(page);
            }
            if (Date.now() - lastBeatAt > 10000) {
                lastBeatAt = Date.now();
                log(`heartbeat qrVisible=${qrVisible} codeInput=${!!visibleCodeInput} modal=${modalSms} smsPhase=${smsPhase} url=${page.url()} text="${(await pageText(page, 160))}"`);
            }

            // 手动动作（curl POST /api/xhs/click）
            if (fs.existsSync(ACTION_FILE)) {
                let act = {};
                try { act = JSON.parse(fs.readFileSync(ACTION_FILE, 'utf-8')); } catch (e) {}
                try { fs.unlinkSync(ACTION_FILE); } catch (e) {}
                log('manual action:', act);
                if (act.action === 'send_code') {
                    const ok = await clickSendCode(page, qrVisible && !modalSms);
                    if (!ok) { await dumpPanel(page, 'manual-send-code'); await screenshot(page, 'manual-send-code'); }
                    setState({
                        polling_state: smsPanelSeen ? 'need_sms' : 'waiting',
                        msg: ok ? '已按你的指令点击「获取验证码」，请查收手机短信并在此输入验证码：'
                                : '未找到可点击的「获取验证码」按钮（已截图留证），请先扫码再试',
                        qr_b64: qrB64
                    });
                } else if (act.action === 'screenshot') {
                    await pumpLiveShot(page);
                } else if (act.action === 'retry_verify' && lastSubmitAt) {
                    const code = (act.code || '').trim();
                    if (/^[0-9]{4,8}$/.test(code)) {
                        log('manual retry_verify, len=' + code.length);
                        const { el: codeEl } = await findSmsCodeInput(page);
                        if (codeEl) { await typeSmsCode(page, codeEl, code); await clickVerify(page, 6000); }
                        else log('retry_verify: 找不到弹窗内验证码输入框');
                    }
                }
            }

            if (smsPhase) {
                if (!smsPanelSeen) {
                    smsPanelSeen = true;
                    log('SMS phase detected, url=', page.url(), 'qrVisible=', qrVisible, 'modal=', modalSms, 'phone=', modalInfo.phone);
                    await dumpPanel(page, 'need-sms');
                    await screenshot(page, 'need-sms');
                    const clicked = modalSms ? await clickSendCode(page, false) : await clickSendCode(page, qrVisible);
                    smsClicks = clicked ? 1 : 0;
                    lastSmsClickAt = Date.now();
                    setState({
                        polling_state: 'need_sms',
                        msg: modalSms
                            ? `小红书已向 ${modalInfo.phone || '你绑定的手机号'} 下发短信验证码，请在下方输入：`
                            : (clicked
                                ? '已自动点击「获取验证码」，请查看手机短信并在下方输入验证码：'
                                : '页面要求短信验证码，但未自动找到可点击的「获取验证码」按钮；如未收到短信，可点「重新发送验证码」按钮让我重试'),
                        qr_b64: ''
                    });
                } else if (modalSms && Date.now() - lastSmsClickAt > 60000 && smsClicks < 3 && Date.now() - lastSubmitAt > 60000) {
                    const clicked = await clickSendCode(page, false);
                    lastSmsClickAt = Date.now();
                    if (clicked) {
                        smsClicks++;
                        log(`re-clicked send-code (#${smsClicks})`);
                        setState({ polling_state: 'need_sms', msg: `已第 ${smsClicks} 次点击「获取验证码」，请查收手机短信并在此输入：`, qr_b64: '' });
                    }
                }

                if (!captchaWarned) {
                    const cap = await hasCaptcha(page);
                    if (cap) {
                        captchaWarned = true;
                        log('captcha / risk-control detected:', cap);
                        await screenshot(page, 'captcha');
                        setState({ polling_state: 'need_sms', msg: `检测到小红书风控验证（${cap}），短信可能未下发；已在服务端截图留证`, qr_b64: '' });
                    }
                }
            }

            // 用户提交了验证码
            if (fs.existsSync(SMS_INPUT_FILE)) {
                try {
                    const smsCode = fs.readFileSync(SMS_INPUT_FILE, 'utf-8').trim();
                    fs.unlinkSync(SMS_INPUT_FILE);
                    if (smsCode) {
                        log('sms code received (len=' + smsCode.length + ')');
                        setState({ polling_state: 'verifying_sms', msg: '正在向小红书提交验证码...', qr_b64: '' });
                        const { el: codeEl, scoped } = await findSmsCodeInput(page);
                        if (codeEl) {
                            submitAttempts++;
                            await typeSmsCode(page, codeEl, smsCode);
                            try {
                                const agreeEle = await page.$("xpath=//div[@class='agreements']//*[local-name()='svg']");
                                if (agreeEle && await agreeEle.isVisible().catch(() => false)) {
                                    await agreeEle.click(); await sleep(300);
                                }
                            } catch (e) {}
                            const clicked = await clickVerify(page, 8000);
                            log('sms 提交结果: clicked=' + clicked + ' attempts=' + submitAttempts + ' scoped=' + scoped);
                            lastSubmitAt = Date.now();
                            await sleep(2500);
                            await dumpPanel(page, 'after-submit');
                            await pumpLiveShot(page);
                            const after = await smsModalInfo(page);
                            if (after.present) {
                                const errHit = (after.text.match(SMS_ERROR_TEXT_RE) || [''])[0];
                                log('提交后弹窗仍在，文案="' + after.text.slice(0, 200) + '" err=' + errHit);
                                setState({
                                    polling_state: 'need_sms',
                                    msg: errHit
                                        ? `小红书返回：${errHit}。请重新输入验证码（或点「重新发送验证码」）：`
                                        : '验证码已提交，但小红书仍需二次确认。若未见变化，请重新输入验证码或点「重新发送验证码」：',
                                    qr_b64: ''
                                });
                            } else {
                                setState({ polling_state: 'verifying_sms', msg: '验证码已提交，正在等待小红书返回登录态...', qr_b64: '' });
                            }
                        } else {
                            log('sms: 提交时找不到弹窗内的验证码输入框');
                            setState({ polling_state: 'need_sms', msg: '没找到验证码输入框（可能弹窗已关闭/已过期），请重新扫码或点「重新发送验证码」：', qr_b64: '' });
                        }
                    }
                } catch (e) { log('Submit sms error:', e.message); }
            }

            // 登录成功判定
            let loggedInByUI = false;
            try {
                loggedInByUI = await page.isVisible("xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']", { timeout: 300 });
            } catch (e) {}

            const currentCookies = await context.cookies();
            const currentDict = {};
            currentCookies.forEach(c => currentDict[c.name] = c.value);
            const currentSession = currentDict['web_session'] || '';
            const sessionChanged = currentSession && currentSession !== unloggedSession;
            const stillNeedsSms = await smsModalEl(page);

            if ((loggedInByUI || (!qrVisible && sessionChanged)) && !stillNeedsSms) {
                const nowStr = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
                const cookieStr = Object.entries(currentDict).map(([k, v]) => `${k}=${v}`).join('; ');
                try { fs.mkdirSync(HERMES_DIR, { recursive: true }); } catch (e) {}
                fs.writeFileSync(XHS_COOKIE_PATH, JSON.stringify({ cookie_dict: currentDict, cookie_str: cookieStr, updated_at: nowStr }, null, 2), { encoding: 'utf-8', mode: 0o600 });

                const profile = await fetchProfile(cookieStr);
                fs.writeFileSync(XHS_META_PATH, JSON.stringify({
                    updated_at: nowStr,
                    status: 'configured',
                    has_a1: !!currentDict['a1'],
                    has_web_session: true,
                    user_nickname: profile.nickname || '小红书已授权用户',
                    user_id: profile.user_id || '',
                    items_count: Object.keys(currentDict).length
                }, null, 2), { encoding: 'utf-8', mode: 0o644 });

                setState({
                    polling_state: 'success',
                    msg: `登录成功！已保存 ${Object.keys(currentDict).length} 个 cookie` + (profile.nickname ? `，账号：${profile.nickname}` : ''),
                    qr_b64: ''
                });
                log('SUCCESS: cookies saved, count=', Object.keys(currentDict).length, 'nickname=', profile.nickname || '(unknown)');
                await sleep(1200);
                break;
            }

            if (Date.now() - startTime > timeoutMs - 15000) {
                log('timeout: 二维码/登录流程超时');
                await dumpPanel(page, 'timeout');
                setState({ polling_state: 'expired', msg: '超时未完成登录，请重新生成二维码再试', qr_b64: '', mode: 'qr' });
                break;
            }
        }
    } catch (e) {
        log('login error:', e.message);
        setState({ polling_state: 'error', msg: '登录流程出错：' + e.message, qr_b64: '' });
    } finally {
        try { if (browser) await browser.close(); } catch (e) {}
        log('=== xhs login run end ===');
    }
}

// ==================== 手机号 + 短信验证码 登录（web 表单模式） ====================
const PHONE_INPUT_SEL = 'div.login-container label.phone > input';
const PHONE_CODE_SEL = 'div.login-container label.auth-code input';
const PHONE_SEND_SEL = 'div.login-container label.auth-code > span.code-button';
const PHONE_SUBMIT_SEL = 'div.login-container button.submit';
const PHONE_AGREE_SEL = 'div.login-container .agreements .agree-icon';
const PHONE_ERR_SEL = 'div.login-container .err-msg';

function maskPhone(p) {
    return String(p).replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');
}

async function formErrText(page) {
    try {
        const el = await page.$(PHONE_ERR_SEL);
        if (!el) return '';
        return ((await el.innerText()) || '').trim();
    } catch (e) { return ''; }
}

async function fillAndSubmit(page, code) {
    try {
        const ci = await page.$(PHONE_CODE_SEL);
        if (ci) {
            await ci.click({ timeout: 2000 });
            await page.fill(PHONE_CODE_SEL, '');
            await page.keyboard.type(String(code), { delay: 60 });
            log('phone mode: code typed');
        } else { log('phone mode: 未找到验证码输入框'); }
    } catch (e) { log('phone mode: fill code error:', e.message); }
    await sleep(500);
    try {
        const sb = await page.$(PHONE_SUBMIT_SEL);
        if (sb) { await sb.click({ timeout: 3000 }); log('phone mode: submit clicked'); }
        else { await page.keyboard.press('Enter'); log('phone mode: submit 按钮未找到，改按 Enter'); }
    } catch (e) { log('phone mode: submit click failed:', e.message); }
}

async function saveLoginSuccess(context, page, mode) {
    try {
        const cookies = await context.cookies();
        const dict = {};
        cookies.forEach(c => dict[c.name] = c.value);
        if (!dict['web_session']) return false;
        const nowStr = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
        const cookieStr = Object.entries(dict).map(([k, v]) => `${k}=${v}`).join('; ');
        try { fs.mkdirSync(HERMES_DIR, { recursive: true }); } catch (e) {}
        fs.writeFileSync(XHS_COOKIE_PATH, JSON.stringify({ cookie_dict: dict, cookie_str: cookieStr, updated_at: nowStr }, null, 2), { encoding: 'utf-8', mode: 0o600 });
        const profile = await fetchProfile(cookieStr);
        fs.writeFileSync(XHS_META_PATH, JSON.stringify({
            updated_at: nowStr, status: 'configured', has_a1: !!dict['a1'], has_web_session: true,
            user_nickname: profile.nickname || '小红书已授权用户', user_id: profile.user_id || '',
            items_count: Object.keys(dict).length, login_mode: mode || 'qr'
        }, null, 2), { encoding: 'utf-8', mode: 0o644 });
        setState({
            polling_state: 'success',
            msg: `登录成功！已保存 ${Object.keys(dict).length} 个 cookie` + (profile.nickname ? `，账号：${profile.nickname}` : ''),
            qr_b64: '', mode: mode || 'qr'
        });
        log('SUCCESS: cookies saved, count=', Object.keys(dict).length, 'mode=', mode, 'nickname=', profile.nickname || '(unknown)');
        await sleep(1200);
        return true;
    } catch (e) {
        log('saveLoginSuccess error:', e.message);
        return false;
    }
}

// 手机号模式：在登录页右侧「手机号登录」表单走完 手机号 → 获取验证码 → 输入验证码 → 登录
async function runLoginPhone() {
    const phone = (process.env.XHS_PHONE || '').trim();
    const timeoutMs = Number(process.env.XHS_TIMEOUT_MS || 420000);
    const mask = maskPhone(phone);
    if (!/^1[0-9]{10}$/.test(phone)) {
        log('phone mode: 手机号格式无效');
        setState({ polling_state: 'error', msg: '手机号格式不正确（需 11 位大陆手机号）', qr_b64: '', mode: 'phone' });
        return;
    }
    try { if (fs.existsSync(SMS_INPUT_FILE)) fs.unlinkSync(SMS_INPUT_FILE); } catch (e) {}
    try { if (fs.existsSync(ACTION_FILE)) fs.unlinkSync(ACTION_FILE); } catch (e) {}
    try { if (fs.existsSync(LIVE_SHOT)) fs.unlinkSync(LIVE_SHOT); } catch (e) {}

    setState({ polling_state: 'waiting', msg: '正在打开小红书登录页（手机号模式）...', qr_b64: '', mode: 'phone', phone_mask: mask, started_at: Date.now() });

    let browser = null;
    try {
        browser = await chromium.launch({
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        });
        const context = await browser.newContext({
            userAgent: UA, locale: 'zh-CN', timezoneId: 'Asia/Shanghai',
            extraHTTPHeaders: { 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' }
        });
        const page = await context.newPage();
        await page.goto('https://www.xiaohongshu.com/login', { waitUntil: 'networkidle', timeout: 35000 });
        await page.waitForSelector(PHONE_INPUT_SEL, { timeout: 20000 });
        await page.fill(PHONE_INPUT_SEL, phone);
        log('phone mode: phone filled', mask);
        try { await page.click(PHONE_AGREE_SEL, { timeout: 2500 }); log('phone mode: agreement clicked'); }
        catch (e) { log('phone mode: agreement click skipped:', e.message); }

        const initialCookies = await context.cookies();
        const initialDict = {};
        initialCookies.forEach(c => initialDict[c.name] = c.value);
        const unloggedSession = initialDict['web_session'] || '';

        if (process.env.XHS_PHONE_DRY === '1') {
            // 演练模式：只验证「填手机号 + 勾协议 + 获取验证码可点」，不真的发短信
            log('phone mode DRY-RUN: 跳过点击「获取验证码」');
            await pumpLiveShot(page);
            const sendOk = await page.isVisible(PHONE_SEND_SEL);
            setState({
                polling_state: 'need_sms',
                msg: `[DRY-RUN] 页面就绪，手机号已填 ${mask}，「获取验证码」可见=${sendOk}，未真实发短信`,
                qr_b64: '', mode: 'phone', phone_mask: mask, started_at: Date.now()
            });
            await sleep(4000);
            return;
        }

        async function clickSendCode() {
            const el = await firstVisible(page, [PHONE_SEND_SEL]);
            if (!el) return false;
            try { await el.click({ timeout: 3000 }); return true; }
            catch (e) { log('phone mode: send-code click failed:', e.message); return false; }
        }

        const sent = await clickSendCode();
        await sleep(1200);
        await pumpLiveShot(page);
        log('phone mode: send-code clicked =', sent, 'formErr =', await formErrText(page));
        setState({
            polling_state: 'need_sms',
            msg: `已向 ${mask} 请求短信验证码，请在下方输入（3 分钟内有效）：`,
            qr_b64: '', mode: 'phone', phone_mask: mask, started_at: Date.now()
        });

        const startTime = Date.now();
        let lastShotAt = Date.now(), lastBeatAt = 0, lastSubmitAt = 0, lastSendAt = Date.now();
        let sendClicks = sent ? 1 : 0;

        while (Date.now() - startTime < timeoutMs) {
            await sleep(1000);

            if (Date.now() - lastShotAt > 8000) { lastShotAt = Date.now(); await pumpLiveShot(page); }
            if (Date.now() - lastBeatAt > 10000) {
                lastBeatAt = Date.now();
                log(`phone-heartbeat err="${await formErrText(page)}" url=${page.url()} text="${(await pageText(page, 160))}"`);
            }

            // 手动动作：send_code / retry_verify / screenshot
            if (fs.existsSync(ACTION_FILE)) {
                let act = {};
                try { act = JSON.parse(fs.readFileSync(ACTION_FILE, 'utf-8')); } catch (e) {}
                try { fs.unlinkSync(ACTION_FILE); } catch (e) {}
                log('phone mode manual action:', act);
                if (act.action === 'send_code') {
                    const ok = await clickSendCode();
                    if (ok) { sendClicks++; lastSendAt = Date.now(); }
                    setState({ polling_state: 'need_sms', msg: ok ? `已重新请求短信验证码（第 ${sendClicks} 次），请查收 ${mask}：` : '未能点到「获取验证码」（可能还在倒计时）', qr_b64: '', mode: 'phone', phone_mask: mask });
                } else if (act.action === 'retry_verify') {
                    const code = (act.code || '').trim();
                    if (/^[0-9]{4,8}$/.test(code)) {
                        log('phone mode: manual retry_verify len=' + code.length);
                        await fillAndSubmit(page, code);
                        lastSubmitAt = Date.now();
                    }
                } else if (act.action === 'screenshot') {
                    await pumpLiveShot(page);
                }
            }

            // 收到验证码 → 填表提交
            if (fs.existsSync(SMS_INPUT_FILE)) {
                let code = '';
                try { code = fs.readFileSync(SMS_INPUT_FILE, 'utf-8').trim(); fs.unlinkSync(SMS_INPUT_FILE); } catch (e) {}
                if (!/^[0-9]{4,8}$/.test(code)) {
                    setState({ polling_state: 'need_sms', msg: '验证码格式不对（应为 4-8 位数字），请重新输入', qr_b64: '', mode: 'phone', phone_mask: mask });
                } else {
                    log('phone mode: code received len=' + code.length);
                    lastSubmitAt = Date.now();
                    setState({ polling_state: 'verifying_sms', msg: '正在提交验证码并登录...', qr_b64: '', mode: 'phone', phone_mask: mask });
                    await fillAndSubmit(page, code);
                    await sleep(3500);
                    await dumpPanel(page, 'phone-after-submit');
                    await pumpLiveShot(page);
                    const err = await formErrText(page);
                    if (err) {
                        log('phone mode: form error = ' + err);
                        setState({ polling_state: 'need_sms', msg: `小红书返回：${err}。可重新输入验证码，或点「重新发送验证码」：`, qr_b64: '', mode: 'phone', phone_mask: mask });
                    } else {
                        setState({ polling_state: 'verifying_sms', msg: '验证码已提交，正在等待登录态...', qr_b64: '', mode: 'phone', phone_mask: mask });
                    }
                }
            }

            // 65 秒没提交成功 → 自动重发一次（最多 3 次）
            if (sendClicks < 3 && Date.now() - lastSendAt > 65000 && Date.now() - lastSubmitAt > 65000) {
                const ok = await clickSendCode();
                if (ok) {
                    sendClicks++; lastSendAt = Date.now();
                    log(`phone mode: re-clicked send-code (#${sendClicks})`);
                    setState({ polling_state: 'need_sms', msg: `已第 ${sendClicks} 次请求验证码，请查收 ${mask}：`, qr_b64: '', mode: 'phone', phone_mask: mask });
                }
            }

            // 登录成功判定
            const cookies = await context.cookies();
            const dict = {};
            cookies.forEach(c => dict[c.name] = c.value);
            const sessionChanged = dict['web_session'] && dict['web_session'] !== unloggedSession;
            let loggedInByUI = false;
            try { loggedInByUI = await page.isVisible("xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']", { timeout: 300 }); } catch (e) {}
            const leftLogin = !page.url().includes('/login');

            if ((loggedInByUI || leftLogin || sessionChanged) && (await formErrText(page)) === '') {
                const ok = await saveLoginSuccess(context, page, 'phone');
                if (ok) break;
            }
        }

        if (Date.now() - startTime >= timeoutMs) {
            log('phone mode: timeout');
            await dumpPanel(page, 'phone-timeout');
            setState({ polling_state: 'expired', msg: '手机号登录超时，请重新发起', qr_b64: '', mode: 'phone', phone_mask: mask });
        }
    } catch (e) {
        log('phone login error:', e.message);
        setState({ polling_state: 'error', msg: '手机号登录出错：' + e.message, qr_b64: '', mode: 'phone', phone_mask: mask });
    } finally {
        try { if (browser) await browser.close(); } catch (e) {}
        log('=== xhs phone login run end ===');
    }
}

if (process.env.XHS_MODE === 'phone') runLoginPhone();
else runLogin();
