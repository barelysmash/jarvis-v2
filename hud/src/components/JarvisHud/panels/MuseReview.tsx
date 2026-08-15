import { useEffect, useState } from 'react';
import { API_BASE } from '../../../lib/ws';

interface MuseArtifact {
  artifact_id: string;
  candidate_index: number;
  mime_type: string;
  width: number;
  height: number;
}

interface MuseReviewData {
  project_id: string;
  title?: string | null;
  status?: string | null;
  approval?: string | null;
  recommended_artifact_id?: string | null;
  approved_artifact_id?: string | null;
  artifacts?: MuseArtifact[];
}

interface MuseReviewProps {
  data?: MuseReviewData | null;
  selectedArtifactId?: string | null;
  onSelectionChange?: (artifactId: string | null) => void;
}

function artifactUrl(projectId: string, artifactId: string): string {
  const base = API_BASE.replace(/\/$/, '');
  return (
    `${base}/api/muse/projects/${encodeURIComponent(projectId)}` +
    `/artifacts/${encodeURIComponent(artifactId)}/content`
  );
}

export function MuseReview({
  data,
  selectedArtifactId,
  onSelectionChange,
}: MuseReviewProps) {
  const artifacts = data?.artifacts ?? [];
  const preferredId =
    data?.recommended_artifact_id ??
    data?.approved_artifact_id ??
    artifacts[0]?.artifact_id ??
    '';

  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    setDismissed(false);
  }, [data?.project_id, preferredId]);

  const selectedId = selectedArtifactId || preferredId;

  if (!data || dismissed || artifacts.length === 0) {
    return null;
  }

  const selected =
    artifacts.find((artifact) => artifact.artifact_id === selectedId) ??
    artifacts[0];

  const hasReferencedSelection =
    !!selectedArtifactId &&
    artifacts.some(
      (artifact) => artifact.artifact_id === selectedArtifactId,
    );

  return (
    <div
      className="absolute left-1/2 top-1/2 z-40 w-[760px] max-h-[580px]
                 -translate-x-1/2 -translate-y-1/2 overflow-y-auto
                 border border-cyan-400/50 bg-[#001018]/95
                 shadow-[0_0_40px_rgba(0,229,255,0.18)]
                 backdrop-blur-md rounded-sm p-4 pointer-events-auto"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className="text-[10px] tracking-[0.28em] text-cyan-300/60">
            MUSE · ARTIFACT REVIEW
          </div>
          <div className="text-lg text-cyan-100 mt-1">
            {data.title || 'Untitled creative project'}
          </div>
          <div className="flex gap-3 mt-1 text-[10px] uppercase tracking-wider
                          text-cyan-300/60">
            <span>{data.status || 'unknown'}</span>
            <span>approval: {data.approval || 'pending'}</span>
          </div>
        </div>

        <button
          type="button"
          aria-label="Dismiss Muse review"
          onClick={() => {
            setDismissed(true);
            onSelectionChange?.(null);
          }}
          className="text-cyan-300/60 hover:text-cyan-100 text-xl leading-none"
        >
          ×
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {artifacts.map((artifact) => {
          const isSelected =
            hasReferencedSelection &&
            artifact.artifact_id === selected.artifact_id;
          const isRecommended =
            artifact.artifact_id === data.recommended_artifact_id;
          const isApproved =
            artifact.artifact_id === data.approved_artifact_id;

          return (
            <button
              key={artifact.artifact_id}
              type="button"
              aria-pressed={isSelected}
              onClick={() => onSelectionChange?.(artifact.artifact_id)}
              className={[
                'text-left border rounded-sm p-2 transition-all',
                isSelected
                  ? 'border-cyan-200 shadow-[0_0_18px_rgba(0,229,255,0.25)]'
                  : 'border-cyan-500/20 hover:border-cyan-300/60',
              ].join(' ')}
            >
              <div className="aspect-square bg-black/40 overflow-hidden">
                <img
                  src={artifactUrl(data.project_id, artifact.artifact_id)}
                  alt={`Muse candidate ${artifact.candidate_index + 1}`}
                  className="w-full h-full object-contain"
                  draggable={false}
                />
              </div>

              <div className="mt-2 text-xs text-cyan-100">
                Candidate {artifact.candidate_index + 1}
              </div>

              <div className="text-[9px] text-cyan-300/50">
                {artifact.width} × {artifact.height}
              </div>

              {(isRecommended || isApproved) && (
                <div className="mt-1 text-[9px] tracking-wider text-cyan-200/80">
                  {isApproved ? 'APPROVED' : 'MUSE RECOMMENDED'}
                </div>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-4 pt-3 border-t border-cyan-500/20
                      flex justify-between gap-4 text-[10px]">
        <div className="text-cyan-200/80">
          {hasReferencedSelection
            ? `Referenced: Candidate ${selected.candidate_index + 1}`
            : `Viewing: Candidate ${selected.candidate_index + 1} · click to reference`}
        </div>
        <div className="text-cyan-300/45 text-right">
          Selection only · approval requires explicit instruction to JARVIS
        </div>
      </div>
    </div>
  );
}
