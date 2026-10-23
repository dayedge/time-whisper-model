import Foundation
import CoreML
import TimeWhisper
import MODEL_MODULE

let folder = MODEL_ACCESSOR.folder
for name in ["TimeWhisperTagger.mlpackage", "tokenizer.json", "tokenizer_config.json", "labels.json"] {
    precondition(FileManager.default.fileExists(atPath: folder.appendingPathComponent(name).path), name)
}
precondition(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("reference.json").path))
let metadata = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("labels.json"))) as! [String: Any]
precondition(Set(metadata["labels"] as! [String]) == Set(TokenLabel.allCases.map(\.rawValue)))
let tagger = CoreMLTagger(folder: folder)
let first = try await tagger.tag("call tomorrow at 3pm", language: "en")
precondition(!first.tokens.isEmpty)
await tagger.close()
let reloaded = try await tagger.tag("call tomorrow at 3pm", language: "en")
precondition(first.tokens.map(\.label) == reloaded.tokens.map(\.label))
await tagger.close()
