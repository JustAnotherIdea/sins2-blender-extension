class MeshMaterial:
    def __init__(
        self,
        clr,
        nrm,
        msk,
        orm,
        em_factor=None,
        em_hue_factor=None,
        base_color_factor=None,
        roughness_factor=None,
        metallic_factor=None,
        has_transparency=None,
    ):
        self.clr = clr
        self.nrm = nrm
        self.msk = msk
        self.orm = orm
        self.em_factor = em_factor
        self.em_hue_factor = em_hue_factor
        self.base_color_factor = base_color_factor
        self.roughness_factor = roughness_factor
        self.metallic_factor = metallic_factor
        self.has_transparency = has_transparency

    def json(self):
        return {
            key: value
            for key, value in {
                "base_color_texture": self.clr or "",
                "normal_texture": self.nrm or "",
                "mask_texture": self.msk or "",
                "occlusion_roughness_metallic_texture": self.orm or "",
                "emissive_factor": self.em_factor,
                "emissive_hue_strength": self.em_hue_factor or 1,
                "base_color_factor": self.base_color_factor or list((1, 1, 1, 1)),
                "roughness_factor": self.roughness_factor or 1,
                "metallic_factor": self.metallic_factor or 1,
                "has_transparency": self.has_transparency or False,
            }.items()
            if value
        }
