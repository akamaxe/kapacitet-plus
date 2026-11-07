import { useCan, type Action } from "../lib/auth";

/**
 * Why an action is shown disabled, in a line beside it: the roles it takes, when the person
 * signed in holds none of them. Nothing with sign-in off or when the action is theirs to take.
 */
export function RoleNote({ action, id }: { action: Action; id?: string }) {
  const can = useCan(action);
  return can.ok ? null : <p className="role-note" id={id}>{can.reason}</p>;
}
