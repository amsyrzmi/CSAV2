/** No cloud speech fallback or language-pack download. Transcript submission is explicit. */
interface Recognition {
  processLocally: boolean;
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { results: { [index: number]: { [index: number]: { transcript: string } } } }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}
export function localDictation(text: (value: string) => void, ended: () => void, failed: (message: string) => void) {
  const browser = window as typeof window & {
    SpeechRecognition?: new () => Recognition;
    webkitSpeechRecognition?: new () => Recognition;
  };
  const Constructor = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
  if (!window.isSecureContext || !Constructor) throw new Error("Local dictation is unavailable. Type your request instead.");
  const recognition = new Constructor();
  if (!("processLocally" in recognition)) throw new Error("This browser cannot require local speech processing. Type your request instead.");
  recognition.processLocally = true;
  recognition.lang = "en-MY";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.onresult = event => text(event.results[0][0].transcript.slice(0, 2000));
  recognition.onerror = () => failed("Local dictation failed. Check microphone permission and the installed on-device language pack, or type your request.");
  recognition.onend = ended;
  recognition.start();
  return () => { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; recognition.abort(); };
}
export function speakLocal(text: string) {
  const synthesizer = window.speechSynthesis;
  if (!synthesizer) throw new Error("Local speech output is unavailable.");
  const voice = synthesizer.getVoices().find(item => item.localService && item.lang.startsWith("en"));
  if (!voice) throw new Error("No local English voice is available.");
  synthesizer.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 500));
  utterance.voice = voice;
  synthesizer.speak(utterance);
}
