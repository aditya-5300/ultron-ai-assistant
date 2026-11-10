param([switch]$Probe, [switch]$SelfTest, [switch]$DictationSelfTest)
# Windows handles recognition here; the server separately checks what each recognized command may do.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Web.Extensions
if ($Probe) {
    @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object { $_.Culture.Name }) | ConvertTo-Json -Compress
    exit
}
$source = @'
using System;
using System.Speech.Recognition;
using System.Speech.Synthesis;
using System.Web.Script.Serialization;
public sealed class VoiceGrammarDefinition {
    public string[] fixedCommands { get; set; }
    public string[] dictationPrefixes { get; set; }
}
public static class UltronVoiceBridge {
    static readonly object gate = new object();
    static volatile bool speaking;
    static Prompt activePrompt;
    static DateTime quietUntil = DateTime.MinValue;
    static DateTime lastDiagnostic = DateTime.MinValue;
    static DateTime lastLevel = DateTime.MinValue;
    static void CancelSpeech(SpeechSynthesizer speaker) {
        activePrompt = null; speaker.SpeakAsyncCancelAll(); speaking = false;
    }
    static void Rejected(string text, float confidence) {
        if (speaking || (DateTime.UtcNow - lastDiagnostic).TotalSeconds < 5) return;
        lastDiagnostic = DateTime.UtcNow;
        Emit("rejected", String.IsNullOrWhiteSpace(text) ? "No words recognized. Check the input meter while speaking; silence or background noise is not a command." : "Not executed. Recognition candidate: " + text + " (confidence " + confidence.ToString("F2", System.Globalization.CultureInfo.InvariantCulture) + ").");
    }
    static void Emit(string type, string text) {
        lock(gate) { Console.WriteLine(new JavaScriptSerializer().Serialize(new { type = type, text = text })); Console.Out.Flush(); }
    }
    public static void Run(bool selfTest, bool dictationTest, string grammarJson) {
        var definition = new JavaScriptSerializer().Deserialize<VoiceGrammarDefinition>(grammarJson);
        if (definition == null || definition.fixedCommands == null || definition.dictationPrefixes == null) throw new Exception("Voice command grammar is missing or invalid.");
        RecognizerInfo selected = null;
        foreach (var info in SpeechRecognitionEngine.InstalledRecognizers()) {
            if (info.Culture.TwoLetterISOLanguageName == "en") { selected = info; break; }
        }
        if (selected == null) throw new Exception("Install an English Windows speech recognition language pack and retry.");
        using (var engine = new SpeechRecognitionEngine(selected))
        using (var speaker = new SpeechSynthesizer()) {
            speaker.SpeakCompleted += (sender, args) => {
                if (Object.ReferenceEquals(activePrompt, args.Prompt)) {
                    activePrompt = null; speaking = false; quietUntil = DateTime.UtcNow.AddMilliseconds(400);
                    Emit("listening", "Listening for Ultron");
                }
            };
            var action = new GrammarBuilder(); action.Culture = selected.Culture;
            action.Append(new Choices("open", "launch", "start", "close", "quit", "exit"));
            action.Append("the", 0, 1);
            action.Append(new Choices("YouTube", "you tube", "Google", "GitHub", "VS Code", "Visual Studio Code", "Notepad", "Calculator", "Paint", "Explorer", "File Explorer", "File Manager", "Windows Explorer", "Windows File Manager", "Task Manager", "taskmanager", "task mgr", "WhatsApp", "Spotify", "Telegram", "Instagram", "Gmail"));
            var commands = new Choices(); commands.Add(action);
            commands.Add(definition.fixedCommands);
            commands.Add(new string[] { "open new tab", "open a new tab", "create a new tab", "browser open new tab", "new tab" });
            var letters = "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z".Split(' ');
            var driveNames = new string[letters.Length];
            for (int index = 0; index < letters.Length; index++) driveNames[index] = letters[index] + " drive";
            foreach (var driveName in driveNames) {
                commands.Add(new string[] { "open " + driveName, "open the " + driveName, "go to " + driveName, "open File Manager and " + driveName, "open File Explorer to " + driveName, "open File Explorer in " + driveName });
            }
            commands.Add(new string[] { "end the WhatsApp task", "stop WhatsApp", "scan the PC", "scan my computer", "scan the PC and check for any malicious files", "run a security scan", "run a malware scan", "run a Defender quick scan", "stop", "cancel", "wait", "stop listening", "disable voice" });
            var command = new GrammarBuilder(commands); command.Culture = selected.Culture;
            var continuation = new GrammarBuilder(); continuation.Culture = selected.Culture;
            continuation.Append(new Choices("and", "then", "and then"));
            var repeatedWake = new GrammarBuilder(new Choices("Ultron", "hey Ultron")); repeatedWake.Culture = selected.Culture;
            continuation.Append(repeatedWake, 0, 1); continuation.Append(command);
            var builder = new GrammarBuilder(); builder.Culture = selected.Culture;
            builder.Append(new Choices("Ultron", "hey Ultron")); builder.Append(command); builder.Append(continuation, 0, 5);
            var grammar = new Grammar(builder); grammar.Name = "desktop-commands"; grammar.Priority = 50;
            engine.LoadGrammar(grammar);
            var common = new GrammarBuilder(); common.Culture = selected.Culture;
            common.Append(new Choices("Ultron", "hey Ultron"));
            common.Append(new Choices("open YouTube", "open the Notepad", "open Notepad", "close YouTube and open VS Code", "open YouTube and Ultron open the Notepad"));
            var commonGrammar = new Grammar(common); commonGrammar.Name = "common-desktop-commands"; commonGrammar.Priority = 100;
            engine.LoadGrammar(commonGrammar);
            bool dictationAvailable = false;
            try {
                var freeText = new GrammarBuilder(); freeText.Culture = selected.Culture;
                freeText.Append(new Choices("Ultron", "hey Ultron"));
                freeText.Append(new Choices(definition.dictationPrefixes));
                freeText.AppendDictation();
                var freeGrammar = new Grammar(freeText); freeGrammar.Name = "bounded-command-dictation";
                engine.LoadGrammar(freeGrammar);
                dictationAvailable = true;
                Emit("capabilities", "Fixed commands and wake-word-prefixed dictation available. Exact paths, URLs and coordinates should be checked before approval.");
            } catch (Exception) {
                Emit("capabilities", "Fixed commands available. English dictation could not load; type file paths, browser fields, research goals and other free-text commands in the assistant.");
            }
            if (dictationTest && !dictationAvailable) throw new Exception("Bounded dictation is unavailable for this Windows recognizer.");
            var wake = new GrammarBuilder(); wake.Culture = selected.Culture;
            wake.Append(new Choices("Ultron", "hey Ultron")); engine.LoadGrammar(new Grammar(wake));
            var recognized = new System.Collections.Generic.List<string>();
            engine.AudioLevelUpdated += (sender, args) => {
                if (selfTest || speaking) return;
                lock(gate) {
                    if ((DateTime.UtcNow - lastLevel).TotalMilliseconds < 750) return;
                    lastLevel = DateTime.UtcNow;
                    Emit("level", args.AudioLevel.ToString(System.Globalization.CultureInfo.InvariantCulture));
                }
            };
            engine.AudioSignalProblemOccurred += (sender, args) => {
                if (!selfTest && !speaking) Emit("signal", args.AudioSignalProblem.ToString());
            };
            engine.SpeechRecognized += (sender, args) => {
                var controlText = args.Result.Text.Trim().ToLowerInvariant();
                bool control = controlText == "ultron stop" || controlText == "hey ultron stop" || controlText == "ultron stop listening" || controlText == "hey ultron stop listening" || controlText == "ultron cancel" || controlText == "hey ultron cancel" || controlText == "ultron wait" || controlText == "hey ultron wait" || controlText == "ultron disable voice" || controlText == "hey ultron disable voice";
                if (control && args.Result.Confidence >= 0.65) {
                    // Stop controls must still work while the assistant is speaking.
                    CancelSpeech(speaker);
                    if (selfTest) recognized.Add(args.Result.Text);
                    Emit("command", args.Result.Text);
                    if (!selfTest && (controlText.EndsWith("stop listening") || controlText.EndsWith("disable voice"))) engine.RecognizeAsyncCancel();
                }
                else if (!speaking && DateTime.UtcNow >= quietUntil && args.Result.Confidence >= 0.65) { if (selfTest) recognized.Add(args.Result.Text); Emit("command", args.Result.Text); }
                else if (!speaking) Rejected(args.Result.Text, args.Result.Confidence);
            };
            var completed = new System.Threading.ManualResetEvent(false);
            engine.RecognizeCompleted += (sender, args) => { Emit("ended", args.Error == null ? "Recognition ended" : args.Error.Message); completed.Set(); };
            engine.SpeechRecognitionRejected += (sender, args) => Rejected(args.Result == null ? "unrecognized speech" : args.Result.Text, args.Result == null ? 0 : args.Result.Confidence);
            System.IO.MemoryStream testAudio = null;
            if (selfTest) {
                // Generated speech tests the engine without opening the user's microphone or any apps.
                speaker.SetOutputToNull();
                testAudio = new System.IO.MemoryStream();
                using (var testSpeaker = new SpeechSynthesizer()) {
                    testSpeaker.SetOutputToWaveStream(testAudio);
                    var prompt = new PromptBuilder(selected.Culture);
                    if (dictationTest) {
                        foreach (var phrase in new string[] { "Ultron find files named report in documents", "Ultron browser click one", "Ultron browser fill two with hello", "Ultron phone tap 300 500", "Ultron phone type hello", "Ultron research solid state drives" }) {
                            prompt.AppendText(phrase); prompt.AppendBreak(TimeSpan.FromSeconds(2));
                        }
                    } else {
                    prompt.AppendText("Ultron open YouTube"); prompt.AppendBreak(TimeSpan.FromSeconds(2));
                    prompt.AppendText("Ultron open new tab"); prompt.AppendBreak(TimeSpan.FromSeconds(2));
                    prompt.AppendText("Ultron open Documents"); prompt.AppendBreak(TimeSpan.FromSeconds(1));
                    prompt.AppendText("Ultron browser inspect"); prompt.AppendBreak(TimeSpan.FromSeconds(1));
                    prompt.AppendText("Ultron phone back"); prompt.AppendBreak(TimeSpan.FromSeconds(1));
                    prompt.AppendText("Ultron close YouTube and open VS Code");
                    prompt.AppendBreak(TimeSpan.FromSeconds(2)); prompt.AppendText("Ultron open the Notepad");
                    prompt.AppendBreak(TimeSpan.FromSeconds(2)); prompt.AppendText("Ultron open YouTube and Ultron open the Notepad");
                    prompt.AppendBreak(TimeSpan.FromSeconds(2)); prompt.AppendText("Ultron open File Manager and D drive");
                    prompt.AppendBreak(TimeSpan.FromSeconds(2)); prompt.AppendText("Ultron stop");
                    prompt.AppendBreak(TimeSpan.FromSeconds(2)); prompt.AppendText("Ultron stop listening");
                    }
                    testSpeaker.Speak(prompt); testSpeaker.SetOutputToNull();
                }
                testAudio.Position = 0; engine.SetInputToWaveStream(testAudio);
            } else engine.SetInputToDefaultAudioDevice();
            engine.RecognizeAsync(RecognizeMode.Multiple);
            if (selfTest) {
                if (!completed.WaitOne(60000)) throw new Exception("Speech recognition audio test timed out.");
                if (dictationTest) {
                    foreach (var prefix in new string[] { "Ultron find files", "Ultron browser click", "Ultron browser fill", "Ultron phone tap", "Ultron phone type", "Ultron research" }) {
                        if (!recognized.Exists(value => value.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))) throw new Exception("Dictation speech test did not recognize: " + prefix);
                    }
                    Emit("test", "Bounded dictation recognition passed. These are synthetic transcripts, not a microphone test.");
                    testAudio.Dispose(); completed.Dispose(); return;
                }
                bool repeatedWakeRecognized = recognized.Contains("Ultron open YouTube and Ultron open the Notepad") || (recognized.FindAll(value => value == "Ultron open YouTube").Count >= 2 && recognized.FindAll(value => value == "Ultron open the Notepad").Count >= 2);
                if (!recognized.Contains("Ultron open YouTube") || !recognized.Contains("Ultron close YouTube and open VS Code") || !recognized.Contains("Ultron open the Notepad") || !repeatedWakeRecognized) throw new Exception("Speech audio test failed: expected app commands were not recognized.");
                if (!recognized.Contains("Ultron open File Manager and D drive")) throw new Exception("Drive navigation speech test failed.");
                if (!recognized.Contains("Ultron open new tab")) throw new Exception("New tab speech test failed.");
                if (!recognized.Contains("Ultron open Documents") || !recognized.Contains("Ultron browser inspect") || !recognized.Contains("Ultron phone back")) throw new Exception("Extended fixed-command speech test failed.");
                if (!recognized.Contains("Ultron stop") || !recognized.Contains("Ultron stop listening")) throw new Exception("Stop controls were not recognized as distinct phrases.");
                activePrompt = new Prompt(new string('x', 10000)); speaking = true;
                speaker.SpeakAsync(activePrompt);
                if (!System.Threading.SpinWait.SpinUntil(() => speaker.State == SynthesizerState.Speaking, 1000)) throw new Exception("Async speech test did not start.");
                CancelSpeech(speaker);
                if (!System.Threading.SpinWait.SpinUntil(() => speaker.State == SynthesizerState.Ready, 1000)) throw new Exception("Speech cancellation did not complete promptly.");
                Emit("test", "Async speech cancellation passed; test audio was not played.");
                testAudio.Dispose(); completed.Dispose(); return;
            }
            Emit("ready", "Windows microphone active: " + selected.Culture.Name);
            string line;
            while ((line = Console.ReadLine()) != null) {
                if (line == "stop") { CancelSpeech(speaker); break; }
                if (line == "interrupt") { CancelSpeech(speaker); Emit("listening", "Interrupted. Listening for Ultron"); continue; }
                if (line.StartsWith("say:")) {
                    activePrompt = null; speaker.SpeakAsyncCancelAll();
                    speaking = true;
                    activePrompt = new Prompt(line.Substring(4));
                    speaker.SpeakAsync(activePrompt);
                }
            }
            engine.RecognizeAsyncCancel();
        }
    }
}
'@
try {
    Add-Type -TypeDefinition $source -ReferencedAssemblies System.Speech,System.Web.Extensions
    $grammarJson = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\lib\voiceGrammar.json') -Raw
    [UltronVoiceBridge]::Run(($SelfTest.IsPresent -or $DictationSelfTest.IsPresent), $DictationSelfTest.IsPresent, $grammarJson)
} catch {
    @{type='fatal'; text=$_.Exception.GetBaseException().Message} | ConvertTo-Json -Compress
    exit 1
}
