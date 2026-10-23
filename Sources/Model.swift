import Foundation

private final class BundleMarker: NSObject {}

/// The model, tokenizer and labels shipped together in this product.
public enum MODEL_ACCESSOR {
    public static var folder: URL {
        guard let folder = Bundle(for: BundleMarker.self).url(forResource: "TimeWhisperTagger", withExtension: nil) else {
            preconditionFailure("The model framework is missing its TimeWhisperTagger resources")
        }
        return folder
    }
}
