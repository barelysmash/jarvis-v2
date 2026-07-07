interface DataWidgetProps {
  title: string;
  data: any;
}

export function DataWidget({ title, data }: DataWidgetProps) {
  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="px-4 py-2 border-b border-cyan-500/20">
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          {title}
        </div>
      </div>
      <div className="p-4 text-sm font-mono text-cyan-200">
        {data ? (
          <pre className="whitespace-pre-wrap text-xs">
            {typeof data === "string" ? data : JSON.stringify(data, null, 2)}
          </pre>
        ) : (
          <span className="text-cyan-700">No data</span>
        )}
      </div>
    </div>
  );
}
