import AgentAvatar from "../fs/AgentAvatar";

/**
 * Shown while the server is working on a turn (`isWorking`): the assistant in its working pose
 * beside plain words. A turn being processed is a state the server confirmed, so the animation is
 * truthful; the words carry the state on their own (the pose is decoration and stops under
 * reduced motion or the "Reduce animations" preference).
 */
export function WorkingRow() {
  return <div data-testid="chat-working" className="flex items-center gap-3">
    <AgentAvatar agentId="assistant" size={48} state="working" label="" />
    <p className="text-[color:var(--fs-text-secondary)]">Working on it</p>
  </div>;
}
