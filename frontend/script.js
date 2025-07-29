document.addEventListener("DOMContentLoaded", () => {
    // --- All previous DOM element selections and constants remain the same ---
    const chatMessages = document.getElementById("chat-messages");
    const textInput = document.getElementById("text-input");
    const sendButton = document.getElementById("send-button");
    const recordButton = document.getElementById("record-button");
// script.js
    const API_BASE_URL = "http://localhost:8001";
    const WEBSOCKET_URL = "ws://localhost:8001/ws/chat";


    let conversationHistory = [];
    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;
    let websocket;
    let audioContext = new (window.AudioContext || window.webkitAudioContext)();

    // --- NEW: Function to make the browser speak text ---
    function speakText(text) {
        // Stop any currently speaking utterance
        window.speechSynthesis.cancel();

        // Clean the text for better speech (e.g., remove markdown)
        const cleanText = text.replace(/(\*\*|__|\*|_)/g, "");

        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = 'en-US'; // Set language to US English
        utterance.rate = 0.95; // Slightly slower for clarity
        
        // Find a suitable voice
        const voices = window.speechSynthesis.getVoices();
        utterance.voice = voices.find(voice => voice.name === 'Google US English' || voice.name === 'Samantha' || voice.lang === 'en-US');

        window.speechSynthesis.speak(utterance);
    }
    // Pre-load voices
    window.speechSynthesis.onvoiceschanged = () => {
        speakText(""); // A trick to ensure voices are loaded on some browsers
    };


    function connectWebSocket() {
        console.log("Attempting to connect to WebSocket...");
        websocket = new WebSocket(WEBSOCKET_URL);

        websocket.onopen = () => {
            console.log("WebSocket connection established.");
        };

        websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'chat_response') {
                conversationHistory = data.conversation_history;
                displayMessage(data.ai_response, 'assistant');
                // --- ADDED: Speak the AI's response ---
                speakText(data.ai_response);
            } else if (data.type === 'error') {
                displayMessage(`Server Error: ${data.message}`, 'error');
            }
        };

        websocket.onerror = (error) => {
            console.error("WebSocket error:", error);
            displayMessage("WebSocket connection error. Check backend and refresh.", 'error');
        };

        websocket.onclose = () => {
            console.log("WebSocket connection closed. Attempting to reconnect...");
            setTimeout(connectWebSocket, 3000);
        };
    }

    function displayMessage(message, role) {
        const messageDiv = document.createElement("div");
        messageDiv.classList.add("message");
        if (role === 'user') {
            messageDiv.classList.add("user-message");
        } else if (role === 'assistant') {
            messageDiv.classList.add("assistant-message");
        } else {
            messageDiv.classList.add("error-message");
        }
        messageDiv.textContent = message;
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function sendTextMessage() {
        const message = textInput.value.trim();
        if (!message) return;
        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            displayMessage("Not connected to the server.", 'error');
            return;
        }
        // Stop any speaking when the user sends a new message
        window.speechSynthesis.cancel();
        displayMessage(message, 'user');
        textInput.value = "";

        websocket.send(JSON.stringify({ type: "text_message", content: message }));
    }

    async function sendAudioMessage(audioBlob) {
        // Stop any speaking when the user sends a new message
        window.speechSynthesis.cancel();
        displayMessage("[Sending audio...]", "user");

        const formData = new FormData();
        formData.append("audio", audioBlob, "user_audio.wav");
        formData.append("conversation_history", JSON.stringify(conversationHistory));

        try {
            const response = await fetch(`${API_BASE_URL}/chat/audio`, {
                method: "POST",
                body: formData,
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.detail || "Failed to process audio.");
            }
            if (result.error) {
                throw new Error(result.error);
            }

            const userMessages = chatMessages.querySelectorAll('.user-message');
            const lastUserMessage = userMessages[userMessages.length - 1];
            if (lastUserMessage?.textContent === "[Sending audio...]") {
                lastUserMessage.textContent = `🎤: "${result.transcription}"`;
            }

            displayMessage(result.response, 'assistant');
            // --- ADDED: Speak the AI's response ---
            speakText(result.response);
            conversationHistory = result.conversation_history;

        } catch (error) {
            console.error("Error sending audio:", error);
            displayMessage(`Error: ${error.message}`, 'error');
        }
    }

    // --- No changes to WAV conversion functions or toggleRecording ---
    async function convertBlobToWav(blob) {
        const arrayBuffer = await blob.arrayBuffer();
        const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
        const wavBlob = encodeWav(audioBuffer);
        return wavBlob;
    }

    function encodeWav(audioBuffer) {
        const numOfChan = audioBuffer.numberOfChannels;
        const length = audioBuffer.length * numOfChan * 2 + 44;
        const buffer = new ArrayBuffer(length);
        const view = new DataView(buffer);
        let pos = 0;

        function setUint16(data) { view.setUint16(pos, data, true); pos += 2; }
        function setUint32(data) { view.setUint32(pos, data, true); pos += 4; }

        setUint32(0x46464952); // "RIFF"
        setUint32(length - 8);
        setUint32(0x45564157); // "WAVE"
        setUint32(0x20746d66); // "fmt "
        setUint32(16);
        setUint16(1);
        setUint16(numOfChan);
        setUint32(audioBuffer.sampleRate);
        setUint32(audioBuffer.sampleRate * 2 * numOfChan);
        setUint16(numOfChan * 2);
        setUint16(16);
        setUint32(0x61746164); // "data"
        setUint32(length - pos - 4);

        const channels = [];
        for (let i = 0; i < numOfChan; i++) {
            channels.push(audioBuffer.getChannelData(i));
        }

        let offset = 0;
        while (pos < length) {
            for (let i = 0; i < numOfChan; i++) {
                let sample = Math.max(-1, Math.min(1, channels[i][offset]));
                sample = (0.5 + sample < 0 ? sample * 32768 : sample * 32767) | 0;
                view.setInt16(pos, sample, true);
                pos += 2;
            }
            offset++;
        }
        return new Blob([view], { type: 'audio/wav' });
    }
    
    async function toggleRecording() {
        if (isRecording) {
            mediaRecorder.stop();
            recordButton.classList.remove("recording");
            recordButton.textContent = "Record";
            isRecording = false;
        } else {
            try {
                if (audioContext.state === 'suspended') { await audioContext.resume(); }
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = (event) => audioChunks.push(event.data);
                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const wavBlob = await convertBlobToWav(audioBlob);
                    sendAudioMessage(wavBlob);
                    stream.getTracks().forEach(track => track.stop());
                };
                mediaRecorder.start();
                recordButton.classList.add("recording");
                recordButton.textContent = "Stop";
                isRecording = true;
            } catch (error) {
                console.error("Error accessing microphone:", error);
                displayMessage("Could not access microphone. Please grant permission.", 'error');
            }
        }
    }

    sendButton.addEventListener("click", sendTextMessage);
    textInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") sendTextMessage();
    });
    recordButton.addEventListener("click", toggleRecording);

    connectWebSocket();
});