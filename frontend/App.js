import React, { useState, useRef } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  ScrollView,
  SafeAreaView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import axios from 'axios';
import Markdown from 'react-native-markdown-display';

// ─────────────────────────────────────────────────────────────
//  Config API
// ─────────────────────────────────────────────────────────────
const API_URL = Platform.select({
  web: 'http://localhost:8000',
  default: 'http://localhost:8000', // remplace par l'IP de ta machine sur mobile
});

// ─────────────────────────────────────────────────────────────
//  Actions rapides — mappées sur les tools du backend
// ─────────────────────────────────────────────────────────────
const QUICK_ACTIONS = [
  {
    icon: '📧',
    label: 'Résume mes mails',
    prompt: 'Résume mes derniers emails importants.',
  },
  {
    icon: '📅',
    label: 'Prochaine réunion',
    prompt: 'Quelle est ma prochaine réunion ?',
  },
  {
    icon: '⏰',
    label: "Aujourd'hui",
    prompt: "Qu'ai-je aujourd'hui dans mon agenda ?",
  },
  {
    icon: '📊',
    label: 'Ma semaine',
    prompt: 'Combien de réunions ai-je cette semaine et quelles sont-elles ?',
  },
  {
    icon: '📁',
    label: 'Fichiers Drive',
    prompt: 'Liste mes derniers fichiers Google Drive.',
  },
  {
    icon: '✉️',
    label: 'Répondre à un mail?',
    prompt: 'Quels sont les emails qui nécessitent une réponse?',
  },
  {
    icon: '📝',
    label: 'PV de réunion',
    prompt:
      'Je veux générer un procès-verbal. Demande-moi la transcription ou les notes de la réunion, puis utilise analyze_meeting_transcript et generate_meeting_minutes.',
    fillOnly: true,
  },
  {
    icon: '🎯',
    label: 'Business situation',
    prompt: 'Où en est le business ce mois ?',
  },
  // ─── Recherche ───
  {
    icon: '🔍',
    label: 'Rechercher…',
    prompt: 'Recherche dans ma base de connaissances : ',
    fillOnly: true,
  },
];

// ─────────────────────────────────────────────────────────────
//  Menu déroulant "Réunions" du header
// ─────────────────────────────────────────────────────────────
const MEETING_MENU = [
  {
    icon: '📋',
    label: 'Préparer une réunion',
    prompt:
      'Je veux préparer une réunion. Demande-moi le type et la date, puis utilise prepare_meeting.',
    fillOnly: true,
  },
  {
    icon: '📝',
    label: 'Générer un PV',
    prompt:
      'Je veux générer un procès-verbal de réunion. Demande-moi la transcription puis utilise generate_meeting_minutes.',
    fillOnly: true,
  },
  {
    icon: '🎯',
    label: 'Deadlines (7j)',
    prompt: 'Quelles sont mes deadlines urgentes dans les 7 prochains jours ?',
  },
  {
    icon: '⚠️',
    label: 'Actions en retard',
    prompt: 'Y a-t-il des actions en retard ou bloquées ?',
  },
  {
    icon: '🔄',
    label: "Statut d'une action",
    prompt: 'Où en est mon action ? Dis-moi son ID.',
    fillOnly: true,
  },
];

// ─────────────────────────────────────────────────────────────
//  Composant principal
// ─────────────────────────────────────────────────────────────
export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [audioUploading, setAudioUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [showMeetingMenu, setShowMeetingMenu] = useState(false);
  const scrollViewRef = useRef();

  // ─── Envoi d'un message ───
  const sendMessage = async (customText = null) => {
    const textToSend = (customText ?? input).trim();
    if (!textToSend || loading || audioUploading) return;

    const userMessage = { role: 'user', content: textToSend };
    setMessages((prev) => [...prev, userMessage]);
    if (customText === null) setInput('');
    setLoading(true);

    try {
      const response = await axios.post(`${API_URL}/chat`, {
        message: textToSend,
        history: messages,
      });

      const assistantMessage = {
        role: 'assistant',
        content: response.data.response,
        tool_calls: response.data.tool_calls,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      console.error('Error:', error);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content:
            '❌ Impossible de joindre le serveur. Vérifie que le backend tourne.',
          isError: true,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // ─── Gestion des actions rapides ───
  const handleQuickAction = (action) => {
    if (loading || audioUploading) return;
    if (action.fillOnly) {
      setInput(action.prompt);
    } else {
      sendMessage(action.prompt);
    }
  };

  // ─── Sélection d'un fichier audio ───
  const pickAudioFile = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['audio/*'],
        copyToCacheDirectory: true,
      });

      if (!result.cancelled) {
        if (result.assets && result.assets.length > 0) {
          setSelectedFile(result.assets[0]);
        } else if (result.type === 'success') {
          setSelectedFile(result);
        }
      }
    } catch (error) {
      Alert.alert(
        'Erreur',
        `Impossible de sélectionner le fichier : ${error.message}`
      );
    }
  };

  // ─── Upload + transcription ───
  const uploadAndTranscribeAudio = async () => {
    if (!selectedFile) return;
    setAudioUploading(true);

    try {
      const formData = new FormData();

      if (Platform.OS === 'web') {
        const response = await fetch(selectedFile.uri);
        const blob = await response.blob();
        formData.append('file', blob, selectedFile.name);
      } else {
        formData.append('file', {
          uri: selectedFile.uri,
          type: selectedFile.mimeType || 'audio/mpeg',
          name: selectedFile.name,
        });
      }

      const response = await axios.post(`${API_URL}/transcribe`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const transcriptText =
        response.data?.text || response.data?.transcript || response.data?.result;

      if (!transcriptText) {
        throw new Error('Réponse invalide du serveur');
      }

      // Message utilisateur (upload)
      setMessages((prev) => [
        ...prev,
        {
          role: 'user',
          content: `📁 ${selectedFile.name}`,
          isUpload: true,
        },
      ]);

      // Message transcription
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `🎙️ **Transcription**\n\n${transcriptText}`,
          isTranscription: true,
          filename: selectedFile.name,
        },
      ]);

      setSelectedFile(null);
    } catch (error) {
      console.error('Upload error:', error);
      Alert.alert(
        'Erreur',
        error.response?.data?.detail ||
          `Échec de la transcription : ${error.message}`
      );
    } finally {
      setAudioUploading(false);
    }
  };

  const cancelFileSelection = () => setSelectedFile(null);

  // ─── Rendu d'un message ───
  const renderMessage = (msg, index) => {
    const isUser = msg.role === 'user';

    return (
      <View
        key={index}
        style={[
          styles.messageRow,
          isUser ? styles.messageRowUser : styles.messageRowAssistant,
        ]}
      >
        {!isUser && (
          <View style={[styles.avatar, styles.avatarAssistant]}>
            <Text style={styles.avatarText}>🤖</Text>
          </View>
        )}

        <View
          style={[
            styles.bubble,
            isUser ? styles.bubbleUser : styles.bubbleAssistant,
            msg.isError && styles.bubbleError,
          ]}
        >
          <Markdown
            style={isUser ? markdownStylesUser : markdownStylesAssistant}
          >
            {msg.content}
          </Markdown>

          {msg.tool_calls && msg.tool_calls.length > 0 && (
            <View style={styles.toolCallsContainer}>
              <Text style={styles.toolCallsTitle}>🔧 Outils utilisés</Text>
              {msg.tool_calls.map((call, idx) => (
                <Text key={idx} style={styles.toolCall}>
                  • {call.name}
                </Text>
              ))}
            </View>
          )}
        </View>

        {isUser && (
          <View style={[styles.avatar, styles.avatarUser]}>
            <Text style={styles.avatarText}>👤</Text>
          </View>
        )}
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* ─── Header ─── */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <View style={styles.headerLogo}>
            <Text style={styles.headerLogoText}>🤖</Text>
          </View>
          <View>
            <Text style={styles.headerTitle}>Assistant IA</Text>
            <View style={styles.statusRow}>
              <View style={styles.statusDot} />
              <Text style={styles.headerSubtitle}>
                En ligne · Emails · Drive · Agenda + Réunions + ERP
              </Text>
            </View>
          </View>
        </View>

        {/* Bouton Réunions */}
        <TouchableOpacity
          style={styles.meetingHeaderBtn}
          onPress={() => setShowMeetingMenu((v) => !v)}
          disabled={loading || audioUploading}
        >
          <Text style={styles.meetingHeaderBtnIcon}>📋</Text>
          <Text style={styles.meetingHeaderBtnText}>Réunions</Text>
        </TouchableOpacity>
      </View>

      {/* Menu déroulant Réunions */}
      {showMeetingMenu && (
        <View style={styles.meetingMenu}>
          {MEETING_MENU.map((item, i) => (
            <TouchableOpacity
              key={i}
              style={styles.meetingMenuItem}
              onPress={() => {
                setShowMeetingMenu(false);
                handleQuickAction(item);
              }}
            >
              <Text style={styles.meetingMenuItemIcon}>{item.icon}</Text>
              <Text style={styles.meetingMenuItemText}>{item.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
      )}

      {/* ─── Chat ─── */}
      <ScrollView
        ref={scrollViewRef}
        style={styles.chatContainer}
        contentContainerStyle={styles.chatContent}
        onContentSizeChange={() =>
          scrollViewRef.current?.scrollToEnd({ animated: true })
        }
      >
        {messages.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyEmoji}>👋</Text>
            <Text style={styles.emptyTitle}>Bonjour !</Text>
            <Text style={styles.emptySubtitle}>
              Pose-moi une question ou utilise une action rapide ci-dessous.
            </Text>

            <View style={styles.emptySuggestions}>
              {QUICK_ACTIONS.slice(0, 4).map((action, i) => (
                <TouchableOpacity
                  key={i}
                  style={styles.emptySuggestion}
                  onPress={() => handleQuickAction(action)}
                >
                  <Text style={styles.emptySuggestionIcon}>{action.icon}</Text>
                  <Text style={styles.emptySuggestionText}>{action.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ) : (
          messages.map(renderMessage)
        )}

        {loading && (
          <View style={styles.messageRow}>
            <View style={[styles.avatar, styles.avatarAssistant]}>
              <Text style={styles.avatarText}>🤖</Text>
            </View>
            <View
              style={[styles.bubble, styles.bubbleAssistant, styles.loadingBubble]}
            >
              <ActivityIndicator size="small" color="#4F46E5" />
              <Text style={styles.loadingText}>Réflexion…</Text>
            </View>
          </View>
        )}

        {audioUploading && (
          <View style={styles.messageRow}>
            <View style={[styles.avatar, styles.avatarAssistant]}>
              <Text style={styles.avatarText}>🎙️</Text>
            </View>
            <View
              style={[styles.bubble, styles.bubbleAssistant, styles.loadingBubble]}
            >
              <ActivityIndicator size="small" color="#10B981" />
              <Text style={styles.loadingText}>Transcription en cours…</Text>
            </View>
          </View>
        )}
      </ScrollView>

      {/* ─── Fichier sélectionné ─── */}
      {selectedFile && (
        <View style={styles.fileCard}>
          <View style={styles.fileInfo}>
            <Text style={styles.fileIcon}>🎙️</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.fileName} numberOfLines={1}>
                {selectedFile.name}
              </Text>
              <Text style={styles.fileSize}>
                {selectedFile.size
                  ? (selectedFile.size / 1024 / 1024).toFixed(2)
                  : '?'}{' '}
                MB
              </Text>
            </View>
          </View>
          <View style={styles.fileActions}>
            <TouchableOpacity
              style={[styles.iconBtn, styles.cancelBtn]}
              onPress={cancelFileSelection}
              disabled={audioUploading}
            >
              <Text style={styles.iconBtnText}>✕</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.iconBtn, styles.confirmBtn]}
              onPress={uploadAndTranscribeAudio}
              disabled={audioUploading}
            >
              {audioUploading ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <Text style={styles.iconBtnText}>▶</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* ─── Actions rapides ─── */}
      <View style={styles.quickActionsWrapper}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.quickActionsContent}
        >
          {QUICK_ACTIONS.map((action, i) => (
            <TouchableOpacity
              key={i}
              style={[
                styles.chip,
                (loading || audioUploading) && styles.chipDisabled,
              ]}
              onPress={() => handleQuickAction(action)}
              disabled={loading || audioUploading}
            >
              <Text style={styles.chipIcon}>{action.icon}</Text>
              <Text style={styles.chipText}>{action.label}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {/* ─── Barre de saisie ─── */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.inputContainer}
      >
        <TouchableOpacity
          style={styles.audioButton}
          onPress={pickAudioFile}
          disabled={loading || audioUploading}
        >
          <Text style={styles.audioButtonIcon}>🎙️</Text>
        </TouchableOpacity>

        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Écris un message…"
          placeholderTextColor="#9CA3AF"
          multiline
          editable={!loading && !audioUploading}
        />

        <TouchableOpacity
          style={[
            styles.sendButton,
            (!input.trim() || loading || audioUploading) &&
              styles.sendButtonDisabled,
          ]}
          onPress={() => sendMessage()}
          disabled={!input.trim() || loading || audioUploading}
        >
          <Text style={styles.sendButtonIcon}>➤</Text>
        </TouchableOpacity>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ─────────────────────────────────────────────────────────────
//  Styles
// ─────────────────────────────────────────────────────────────
const COLORS = {
  primary: '#4F46E5',
  primaryDark: '#4338CA',
  success: '#10B981',
  danger: '#EF4444',
  bg: '#F9FAFB',
  card: '#FFFFFF',
  text: '#111827',
  muted: '#6B7280',
  border: '#E5E7EB',
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.bg },

  // Header
  header: {
    paddingHorizontal: 20,
    paddingVertical: 14,
    backgroundColor: COLORS.primary,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', flexShrink: 1 },
  headerLogo: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: 'rgba(255,255,255,0.18)',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  headerLogoText: { fontSize: 22 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: '700' },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginTop: 2 },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: '#4ADE80',
    marginRight: 6,
  },
  headerSubtitle: { color: 'rgba(255,255,255,0.85)', fontSize: 12 },

  // Bouton "Réunions" dans le header
  meetingHeaderBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.18)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    marginLeft: 8,
  },
  meetingHeaderBtnIcon: { fontSize: 16, marginRight: 6 },
  meetingHeaderBtnText: { color: '#fff', fontSize: 13, fontWeight: '600' },

  // Menu déroulant Réunions
  meetingMenu: {
    backgroundColor: COLORS.card,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
    paddingVertical: 6,
    paddingHorizontal: 12,
  },
  meetingMenuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 8,
    borderRadius: 10,
  },
  meetingMenuItemIcon: { fontSize: 18, marginRight: 12 },
  meetingMenuItemText: { fontSize: 14, color: COLORS.text, fontWeight: '500' },

  // Chat
  chatContainer: { flex: 1 },
  chatContent: { padding: 16, paddingBottom: 8 },

  // Message rows
  messageRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginBottom: 14,
  },
  messageRowUser: { justifyContent: 'flex-end' },
  messageRowAssistant: { justifyContent: 'flex-start' },

  avatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    marginHorizontal: 6,
  },
  avatarAssistant: { backgroundColor: '#EEF2FF' },
  avatarUser: { backgroundColor: '#DBEAFE' },
  avatarText: { fontSize: 16 },

  bubble: {
    maxWidth: '78%',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
  },
  bubbleUser: {
    backgroundColor: COLORS.primary,
    borderBottomRightRadius: 4,
  },
  bubbleAssistant: {
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderBottomLeftRadius: 4,
  },
  bubbleError: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
  },

  loadingBubble: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  loadingText: {
    marginLeft: 8,
    color: COLORS.muted,
    fontSize: 13,
  },

  // Tool calls
  toolCallsContainer: {
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  toolCallsTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: COLORS.muted,
    marginBottom: 4,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  toolCall: { fontSize: 12, color: COLORS.text },

  // Empty state
  emptyState: {
    alignItems: 'center',
    paddingTop: 40,
    paddingHorizontal: 20,
  },
  emptyEmoji: { fontSize: 48, marginBottom: 8 },
  emptyTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 6,
  },
  emptySubtitle: {
    fontSize: 14,
    color: COLORS.muted,
    textAlign: 'center',
    marginBottom: 24,
  },
  emptySuggestions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 8,
  },
  emptySuggestion: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: COLORS.card,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    margin: 4,
  },
  emptySuggestionIcon: { marginRight: 6, fontSize: 16 },
  emptySuggestionText: { color: COLORS.text, fontSize: 13, fontWeight: '500' },

  // File card
  fileCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginHorizontal: 12,
    marginBottom: 6,
    padding: 12,
    backgroundColor: '#EEF2FF',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#C7D2FE',
  },
  fileInfo: { flexDirection: 'row', alignItems: 'center', flex: 1 },
  fileIcon: { fontSize: 22, marginRight: 10 },
  fileName: { fontSize: 14, fontWeight: '600', color: COLORS.primaryDark },
  fileSize: { fontSize: 11, color: COLORS.muted, marginTop: 2 },
  fileActions: { flexDirection: 'row', gap: 8 },
  iconBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelBtn: { backgroundColor: COLORS.danger },
  confirmBtn: { backgroundColor: COLORS.success },
  iconBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },

  // Quick actions
  quickActionsWrapper: {
    backgroundColor: COLORS.card,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  quickActionsContent: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: '#F3F4F6',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginRight: 8,
  },
  chipDisabled: { opacity: 0.5 },
  chipIcon: { fontSize: 14, marginRight: 6 },
  chipText: { fontSize: 13, color: COLORS.text, fontWeight: '500' },

  // Input
  inputContainer: {
    flexDirection: 'row',
    padding: 12,
    backgroundColor: COLORS.card,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    alignItems: 'flex-end',
    gap: 8,
  },
  audioButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#ECFDF5',
    alignItems: 'center',
    justifyContent: 'center',
  },
  audioButtonIcon: { fontSize: 20 },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    backgroundColor: '#F3F4F6',
    borderRadius: 22,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 15,
    color: COLORS.text,
  },
  sendButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: COLORS.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendButtonDisabled: { backgroundColor: '#D1D5DB' },
  sendButtonIcon: { color: '#fff', fontSize: 18, fontWeight: '700' },
});

// ─────────────────────────────────────────────────────────────
//  Markdown styles
// ─────────────────────────────────────────────────────────────
const markdownStylesAssistant = {
  body: { fontSize: 15, color: COLORS.text, lineHeight: 21 },
  strong: { fontWeight: '700' },
  em: { fontStyle: 'italic' },
  code_inline: {
    backgroundColor: '#F3F4F6',
    paddingHorizontal: 4,
    borderRadius: 4,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    fontSize: 13,
  },
  fence: {
    backgroundColor: '#F3F4F6',
    padding: 10,
    borderRadius: 8,
    fontSize: 13,
  },
  bullet_list: { marginVertical: 4 },
  ordered_list: { marginVertical: 4 },
};

const markdownStylesUser = {
  ...markdownStylesAssistant,
  body: { fontSize: 15, color: '#fff', lineHeight: 21 },
  code_inline: {
    backgroundColor: 'rgba(255,255,255,0.2)',
    paddingHorizontal: 4,
    borderRadius: 4,
    fontSize: 13,
  },
};