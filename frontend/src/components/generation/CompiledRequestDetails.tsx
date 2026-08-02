import type { CompiledGenerationRequest } from "@/lib/api";
import CompiledRequestContent from "@/components/generation/CompiledRequestContent";

/** Read-only rendering of the immutable request stored with a generation job. */
export default function CompiledRequestDetails({
  compiled,
}: {
  compiled: CompiledGenerationRequest;
}) {
  return <CompiledRequestContent compiled={compiled} />;
}
