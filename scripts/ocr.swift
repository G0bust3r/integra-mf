import AppKit
import Foundation
import PDFKit
import Vision

enum OCRFailure: Error {
    case unsupportedFile(String)
    case missingImage(String)
}

func recognizeText(in cgImage: CGImage) throws -> [String] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["pt-BR", "en-US"]

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])

    let observations = request.results ?? []
    return observations.compactMap { $0.topCandidates(1).first?.string }
}

func imageFromPDFPage(_ page: PDFPage) -> CGImage? {
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.0
    let width = Int(bounds.width * scale)
    let height = Int(bounds.height * scale)
    guard
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
        let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
    else {
        return nil
    }

    context.setFillColor(NSColor.white.cgColor)
    context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
    context.saveGState()
    context.translateBy(x: 0, y: CGFloat(height))
    context.scaleBy(x: scale, y: -scale)
    page.draw(with: .mediaBox, to: context)
    context.restoreGState()
    return context.makeImage()
}

func extractText(from url: URL) throws -> [String] {
    let ext = url.pathExtension.lowercased()
    if ext == "pdf" {
        guard let document = PDFDocument(url: url) else {
            throw OCRFailure.unsupportedFile(url.lastPathComponent)
        }
        var lines: [String] = []
        for index in 0..<document.pageCount {
            guard let page = document.page(at: index) else { continue }
            if let text = page.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                lines.append(contentsOf: text.components(separatedBy: .newlines))
            } else if let image = imageFromPDFPage(page) {
                lines.append(contentsOf: try recognizeText(in: image))
            }
        }
        return lines
    }

    guard let image = NSImage(contentsOf: url) else {
        throw OCRFailure.missingImage(url.lastPathComponent)
    }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw OCRFailure.missingImage(url.lastPathComponent)
    }
    return try recognizeText(in: cgImage)
}

let arguments = CommandLine.arguments.dropFirst()
guard !arguments.isEmpty else {
    fputs("Uso: swift scripts/ocr.swift <arquivo>\n", stderr)
    exit(1)
}

do {
    for argument in arguments {
        let url = URL(fileURLWithPath: argument)
        let lines = try extractText(from: url)
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                print(trimmed)
            }
        }
    }
} catch {
    fputs("Erro no OCR: \(error)\n", stderr)
    exit(1)
}
