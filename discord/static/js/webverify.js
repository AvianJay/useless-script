var fpPromise = FingerprintJS.load()
let fingerprint;

async function generateFingerprint() {
    const fp = await fpPromise;
    const result = await fp.get();
    fingerprint = result.visitorId;
    return fingerprint;
}

async function reCaptchaCallback(token) {
    document.getElementById('captcha-token').value = token;
    await generateFingerprint();
    document.getElementById('fingerprint').value = fingerprint;
    document.getElementById('verify-form').submit();
}

async function turnstileCallback(token) {
    document.getElementById('captcha-token').value = token;
    await generateFingerprint();
    document.getElementById('fingerprint').value = fingerprint;
    document.getElementById('verify-form').submit();
}

async function directVerify() {
    await generateFingerprint();
    document.getElementById('fingerprint').value = fingerprint;
    document.getElementById('verify-form').submit();
}