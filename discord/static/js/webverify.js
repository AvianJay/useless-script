var fpPromise = FingerprintJS.load()
let fingerprint;

function trackEvent(name, params = {}) {
    if (typeof gtag === 'function') {
        gtag('event', name, params);
    }
}

async function generateFingerprint() {
    const fp = await fpPromise;
    const result = await fp.get();
    fingerprint = result.visitorId;
    return fingerprint;
}

async function generateFingerprintWithTimeout(timeout = 2000) {
    return Promise.race([
        generateFingerprint(),
        new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Fingerprint timeout')), timeout)
        )
    ]);
}

async function reCaptchaCallback(token) {
    try {
        trackEvent('captcha_verified', {
            captcha_type: 'recaptcha'
        });
        document.getElementById('captcha-token').value = token;
        await generateFingerprintWithTimeout();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in reCaptchaCallback:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}

async function turnstileCallback(token) {
    try {
        trackEvent('captcha_verified', {
            captcha_type: 'turnstile'
        });
        document.getElementById('captcha-token').value = token;
        await generateFingerprintWithTimeout();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in turnstileCallback:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}

async function directVerify() {
    try {
        trackEvent('captcha_verified', {
            captcha_type: 'direct'
        });
        await generateFingerprintWithTimeout();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in directVerify:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}