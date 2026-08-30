const GOOGLE_CONFIG = {
    SPEECH_API_KEY: import.meta.env.VITE_GOOGLE_SPEECH_API_KEY,
    TTS_API_KEY: import.meta.env.VITE_GOOGLE_TTS_API_KEY,

    STT_ENDPOINT: "https://speech.googleapis.com/v1/speech:recognize",
    TTS_ENDPOINT: "https://texttospeech.googleapis.com/v1/text:synthesize",

    LANGUAGES: {
        "en-IN": {
            label: "English",
            stt_code: "en-IN",
            tts_code: "en-IN",
            tts_voice: "en-IN-Standard-A",
            tts_gender: "FEMALE"
        },
        "hi-IN": {
            label: "Hindi",
            stt_code: "hi-IN",
            tts_code: "hi-IN",
            tts_voice: "hi-IN-Standard-A",
            tts_gender: "FEMALE"
        },
        "kn-IN": {
            label: "Kannada",
            stt_code: "kn-IN",
            tts_code: "kn-IN",
            tts_voice: "kn-IN-Standard-A",
            tts_gender: "FEMALE"
        }
    },

    STT_CONFIG: {
        encoding: "WEBM_OPUS",
        sampleRateHertz: 48000,
        enableAutomaticPunctuation: true,
        model: "latest_long",
        alternativeLanguageCodes: ["en-IN", "hi-IN", "kn-IN"]
    },

    TTS_CONFIG: {
        audioEncoding: "MP3",
        speakingRate: 0.9,
        pitch: 0.0,
        volumeGainDb: 0.0
    }
};

export default GOOGLE_CONFIG;