<#
Re-save a deck through PowerPoint and export a PDF next to it (Windows with PowerPoint installed).

    powershell -ExecutionPolicy Bypass -File scripts/finalize_deck.ps1 build/EdgeCar_TELIPORT_R1.pptx

The deck uses the template's own font (Calibri), which ships with Office and has metric-compatible substitutes
elsewhere. PowerPoint is asked to embed TrueType fonts, but did not embed Calibri on the machine this was built on —
so the PDF is the copy that renders identically everywhere.
#>
param([Parameter(Mandatory = $true)][string]$Deck)

$deck = (Resolve-Path $Deck).Path
$pdf = [IO.Path]::ChangeExtension($deck, ".pdf")
$app = New-Object -ComObject PowerPoint.Application
try {
    $pres = $app.Presentations.Open($deck, $false, $false, $false)  # read-write, untitled = no, no window
    $pres.SaveAs($deck, 24, -1)  # ppSaveAsOpenXMLPresentation, EmbedTrueTypeFonts = msoTrue (where licensable)
    $pres.SaveAs($pdf, 32)       # ppSaveAsPDF
    $pres.Close()
}
finally {
    $app.Quit()
}
Write-Output "saved: $deck ($([math]::Round((Get-Item $deck).Length / 1MB, 1)) MB)"
Write-Output "pdf:   $pdf ($([math]::Round((Get-Item $pdf).Length / 1MB, 1)) MB)"
