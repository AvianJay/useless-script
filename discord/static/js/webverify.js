var fpPromise = FingerprintJS.load()
let fingerprint;

async function generateFingerprint() {
    const fp = await fpPromise;
    const result = await fp.get();
    fingerprint = result.visitorId;
    return fingerprint;
}

async function executereCaptcha(sitekey) {
    grecaptcha.ready(async function () {
        grecaptcha.execute(sitekey, { action: 'submit' }).then(async function (token) {
            try {
                document.getElementById('captcha-token').value = token;
                await generateFingerprint();
                document.getElementById('fingerprint').value = fingerprint;
                document.getElementById('verify-form').submit();
            } catch (error) {
                console.error("Error in executereCaptcha:", error);
                alert(`發生了一些錯誤。${error.message}`);
            }
        });
    });
}

async function reCaptchaCallback(token) {
    try {
        document.getElementById('captcha-token').value = token;
        await generateFingerprint();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in reCaptchaCallback:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}

async function turnstileCallback(token) {
    try {
        document.getElementById('captcha-token').value = token;
        await generateFingerprint();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in turnstileCallback:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}

async function directVerify() {
    try {
        await generateFingerprint();
        document.getElementById('fingerprint').value = fingerprint;
        document.getElementById('verify-form').submit();
    } catch (error) {
        console.error("Error in directVerify:", error);
        alert(`發生了一些錯誤。${error.message}`);
    }
}